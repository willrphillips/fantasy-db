"""Guillotine league rundown and week-N lineup / waiver data, as JSON. Read-only on guillotine.db,
plus one Yahoo players-list pull for current roster / waiver status. No AI, no Discord.

    python -m nfl.greport [--week 12] > cache/greport_w12.json

Method (every number below comes from guillotine.db, scored by Yahoo under THIS league's rules):
  * team week score   = sum of act_pts of the players in a starting slot that week
  * player games      = weeks with an nflverse stats row (gsis map), else his NFL team had a game
  * mean / sd / floor = over those games, weeks 1..N-1; floor = 25th percentile
  * matchup factor    = half-PPR points the opponent allowed per game to that position, weeks
                        1..N-1, divided by the league-wide average for the position (>1 = soft)
  * SOS               = mean matchup factor over the remaining weeks of the guillotine (N, N+1)
  * risk-adj          = Yahoo projection - 0.5 * sd. Guillotine pays for not finishing last, so
                        the floor counts more than the ceiling.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics as st
import sys
from collections import defaultdict

from . import guillotine as g
from .db import connect
from .yahoo_web import YahooWeb

ALIAS = {"JAC": "JAX", "LAR": "LA", "WSH": "WAS"}   # Yahoo -> nflverse team codes
SLOTS = ["QB", "WR", "WR", "RB", "RB", "TE", "W/R/T"]
FLEX = {"WR", "RB", "TE"}
LAMBDA = 0.5


def nflteam(t):
    t = (t or "").upper()
    return ALIAS.get(t, t)


def pos1(p):
    return (p or "").split(",")[0].strip()


def q(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args)]


def pctl(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * p
    f, c = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


SQL_TEAM_WEEKS = """
SELECT r.week, lt.team_id, lt.team_name, lt.is_will,
       ROUND(SUM(COALESCE(a.act_pts, 0)), 2) AS pts,
       ROUND(SUM(COALESCE(p.proj_pts, 0)), 2) AS proj
FROM rosters r JOIN league_teams lt USING (team_key)
LEFT JOIN actuals a ON a.season=r.season AND a.week=r.week AND a.player_key=r.player_key
LEFT JOIN projections p ON p.season=r.season AND p.week=r.week AND p.player_key=r.player_key
WHERE r.slot NOT IN ('BN', 'IR')
GROUP BY r.week, lt.team_id ORDER BY r.week, pts"""

SQL_PLAYER_WEEKS = """
SELECT a.week, a.player_key, pl.name, pl.team, pl.pos, pl.gsis_id, a.act_pts,
       (SELECT 1 FROM stats s WHERE s.gsis_id=pl.gsis_id AND s.week=a.week) AS has_stats
FROM actuals a JOIN players pl USING (player_key)
WHERE a.week < ? AND a.is_final=1"""

SQL_POOL = """
SELECT p.player_key, pl.name, pl.team, pl.pos, p.proj_pts FROM projections p
JOIN players pl USING (player_key) WHERE p.week=? AND p.proj_pts > 0 ORDER BY p.proj_pts DESC
-- minus every player on any week-N roster (done in Python)"""

SQL_ROSTER = """
SELECT r.slot, pl.player_key, pl.name, pl.team, pl.pos, pl.bye_week, p.proj_pts
FROM rosters r JOIN players pl USING (player_key)
LEFT JOIN projections p ON p.season=r.season AND p.week=r.week AND p.player_key=r.player_key
WHERE r.week=? AND r.team_key=?"""


def build(week: int) -> dict:
    conn = connect(g.DB, readonly=True)
    season = conn.execute("SELECT MAX(season) FROM rosters").fetchone()[0]

    # ---------------------------------------------------------------- schedule
    games = q(conn, "SELECT week, home, away, kickoff_utc, home_score, away_score FROM games WHERE season=?", (season,))
    opp, kick = {}, {}
    for gm in games:
        for a, b in ((gm["home"], gm["away"]), (gm["away"], gm["home"])):
            opp[(gm["week"], a)] = b
            kick[(gm["week"], a)] = gm["kickoff_utc"]

    # ---------------------------------------------------------------- teams by week
    tw = q(conn, SQL_TEAM_WEEKS)
    chop = {r["team_id"]: r["chopped_week"] for r in q(conn, "SELECT team_id, chopped_week FROM teams WHERE alive=0")}
    weeks = defaultdict(list)
    for r in tw:
        weeks[r["week"]].append(r)
    rundown = []
    for w in sorted(weeks):
        rows = sorted(weeks[w], key=lambda r: -r["pts"])
        n = len(rows)
        for i, r in enumerate(rows):
            r["rank"] = i + 1
            r["of"] = n
            r["chopped_this_week"] = chop.get(r["team_id"]) == w
        low = rows[-1]
        rundown.append({"week": w, "teams": rows, "lowest": low["team_name"],
                        "chopped": [r["team_name"] for r in rows if r["chopped_this_week"]],
                        "lowest_is_chopped": low["chopped_this_week"] if w < week else None,
                        "margin_low_to_next": round(rows[-2]["pts"] - low["pts"], 2) if n > 1 else None})

    # ---------------------------------------------------------------- player history
    pw = q(conn, SQL_PLAYER_WEEKS, (week,))
    hist = defaultdict(list)
    allowed = defaultdict(lambda: defaultdict(float))   # (def team, pos) -> week -> pts
    for r in pw:
        t = nflteam(r["team"])
        played = bool(r["has_stats"]) if r["gsis_id"] else ((r["week"], t) in opp and (r["act_pts"] or 0) != 0)
        if not played:
            continue
        hist[r["player_key"]].append((r["week"], r["act_pts"] or 0.0))
        o = opp.get((r["week"], t))
        if o:
            allowed[(o, pos1(r["pos"]))][r["week"]] += r["act_pts"] or 0.0
    pos_avg = defaultdict(list)
    for (d, p), byw in allowed.items():
        pos_avg[p].extend(byw.values())
    pos_avg = {p: st.mean(v) for p, v in pos_avg.items() if v}
    factor = {}
    for (d, p), byw in allowed.items():
        if p in pos_avg and pos_avg[p]:
            factor[(d, p)] = round(st.mean(byw.values()) / pos_avg[p], 3)

    def metrics(pk, name, team, pos, proj):
        xs = [v for _, v in sorted(hist.get(pk, []))]
        t, p = nflteam(team), pos1(pos)
        o1, o2 = opp.get((week, t)), opp.get((week + 1, t))
        f1 = factor.get((o1, p)) if o1 else None
        f2 = factor.get((o2, p)) if o2 else None
        sd = round(st.pstdev(xs), 2) if len(xs) >= 2 else None
        return {"player_key": pk, "name": name, "team": team, "pos": pos, "proj": proj,
                "games": len(xs), "mean": round(st.mean(xs), 2) if xs else None, "sd": sd,
                "floor_p25": round(pctl(xs, 0.25), 2) if xs else None,
                "min": min(xs) if xs else None, "last3": xs[-3:],
                "opp": o1 or "BYE", "kickoff_utc": kick.get((week, t)), "matchup": f1,
                "opp_next": o2 or "BYE", "matchup_next": f2,
                "sos": round(st.mean([x for x in (f1, f2) if x is not None]), 3) if (f1 or f2) else None,
                "risk_adj": round((proj or 0) - LAMBDA * (sd if sd is not None else 0), 2)}

    # ---------------------------------------------------------------- Will's roster
    tk = f"{g.LEAGUE_ID}.t.{g.WILL_TEAM_ID}"
    mine = [dict(metrics(r["player_key"], r["name"], r["team"], r["pos"], r["proj_pts"]), slot=r["slot"])
            for r in q(conn, SQL_ROSTER, (week, tk))]
    def best_lineup(players):
        out, taken = [], set()
        for slot in SLOTS:
            ok = FLEX if slot == "W/R/T" else {slot}
            cands = [m for m in players if m["player_key"] not in taken and pos1(m["pos"]) in ok
                     and m["opp"] != "BYE" and (m["proj"] or 0) > 0]
            if not cands:
                out.append({"slot": slot, "name": None})
                continue
            best = max(cands, key=lambda m: m["risk_adj"])
            taken.add(best["player_key"])
            out.append({**best, "slot": slot, "current_slot": best.get("slot", "waiver")})
        return out, taken

    lineup, used = best_lineup(mine)
    for m in mine:
        m["recommended"] = "START" if m["player_key"] in used else "SIT"
        m["change"] = (m["recommended"] == "START") != (m["slot"] not in ("BN", "IR"))

    # ---------------------------------------------------------------- waiver pool
    rostered = {r["player_key"] for r in q(conn, "SELECT player_key FROM rosters WHERE week=?", (week,))}
    chopped_team = [t for t, w in chop.items() if w == week - 1]
    freshly = {r["player_key"] for r in q(
        conn, "SELECT player_key FROM rosters WHERE week=? AND team_key IN (%s)" % ",".join("?" * len(chopped_team)),
        [week - 1] + [f"{g.LEAGUE_ID}.t.{t}" for t in chopped_team])} if chopped_team else set()
    status = {}
    try:
        for p in YahooWeb(g.LEAGUE_ID).players(week, "proj", ("O",), {"O": 150}).values():
            status[p["player_key"]] = p.get("roster_status")
    except Exception as e:  # noqa: BLE001
        status["_error"] = str(e)
    pool = []
    for r in q(conn, SQL_POOL, (week,)):
        if r["player_key"] in rostered:
            continue
        m = metrics(r["player_key"], r["name"], r["team"], r["pos"], r["proj_pts"])
        m["from_chopped_team"] = r["player_key"] in freshly
        m["yahoo_status"] = status.get(r["player_key"])
        pool.append(m)
    pool.sort(key=lambda m: -m["risk_adj"])

    # ---------------------------------------------------------------- recompute check
    sc = json.loads(conn.execute("SELECT scoring_json FROM league").fetchone()[0])
    recompute = q(conn, """
        SELECT a.week, COUNT(*) AS n,
               SUM(ABS(a.act_pts - (COALESCE(s.pass_yds,0)/25.0 + 4*COALESCE(s.pass_td,0) - COALESCE(s.pass_int,0)
                 + COALESCE(s.rush_yds,0)/10.0 + 6*COALESCE(s.rush_td,0) + 0.5*COALESCE(s.rec,0)
                 + COALESCE(s.rec_yds,0)/10.0 + 6*COALESCE(s.rec_td,0) + 2*COALESCE(s.two_pt,0)
                 - 2*COALESCE(s.fum_lost,0))) <= 0.11) AS match_half_ppr,
               SUM(ABS(a.act_pts - (COALESCE(s.pass_yds,0)/25.0 + 4*COALESCE(s.pass_td,0) - COALESCE(s.pass_int,0)
                 + COALESCE(s.rush_yds,0)/10.0 + 6*COALESCE(s.rush_td,0) + 1.0*COALESCE(s.rec,0)
                 + COALESCE(s.rec_yds,0)/10.0 + 6*COALESCE(s.rec_td,0) + 2*COALESCE(s.two_pt,0)
                 - 2*COALESCE(s.fum_lost,0))) <= 0.11) AS match_full_ppr
        FROM actuals a JOIN players pl USING (player_key)
        JOIN stats s ON s.gsis_id=pl.gsis_id AND s.week=a.week AND s.season=a.season
        WHERE a.week < ? AND COALESCE(s.rec,0) > 0 GROUP BY a.week""", (week,))

    # ---------------------------------------------------------------- chop risk this week
    # each alive team's current starters: proj sum and sd = sqrt(sum of player sd^2) (players treated as
    # independent); then P(lowest) by Monte Carlo on normals, current lineup vs the recommended one.
    def team_line(players):
        proj = sum(m["proj"] or 0 for m in players)
        sd = math.sqrt(sum((m["sd"] if m["sd"] is not None else 6.0) ** 2 for m in players))
        return round(proj, 2), round(sd, 2)

    teams_now = {}
    for t in q(conn, "SELECT team_id, team_name, is_will FROM teams WHERE alive=1"):
        ps = [metrics(r["player_key"], r["name"], r["team"], r["pos"], r["proj_pts"])
              for r in q(conn, SQL_ROSTER, (week, f"{g.LEAGUE_ID}.t.{t['team_id']}")) if r["slot"] not in ("BN", "IR")]
        teams_now[t["team_name"]] = {"is_will": t["is_will"], "starters": len(ps), "proj_sd": team_line(ps)}
    rec = [m for m in lineup if m.get("name")]
    will_name = next(n for n, t in teams_now.items() if t["is_will"])

    def p_lowest(will_line, n=40000):
        rnd = random.Random(12)
        lines = {n_: t["proj_sd"] for n_, t in teams_now.items()}
        lines[will_name] = will_line
        low = 0
        for _ in range(n):
            draw = {k: rnd.gauss(mu, sd) for k, (mu, sd) in lines.items()}
            low += min(draw, key=draw.get) == will_name
        return round(low / n, 3)

    chop_risk = {"teams": teams_now,
                 "will_current": {"proj_sd": teams_now[will_name]["proj_sd"],
                                  "p_lowest": p_lowest(teams_now[will_name]["proj_sd"])},
                 "will_recommended": {"proj_sd": team_line(rec), "p_lowest": p_lowest(team_line(rec))}}
    # what each candidate would do for Will alone (one claim at a time): the freshly released players plus
    # the top 8 unrostered at each position. Same random draws every time, so the differences are real.
    by_pos = defaultdict(list)
    for m in pool:
        by_pos[pos1(m["pos"])].append(m)
    base_p = chop_risk["will_recommended"]["p_lowest"]
    cands = {m["player_key"]: m for m in pool if m["from_chopped_team"]}
    for v in by_pos.values():
        for m in v[:8]:
            cands[m["player_key"]] = m
    claims = []
    for c in cands.values():
        lu, _ = best_lineup(mine + [dict(c, slot="waiver")])
        if not any(x.get("player_key") == c["player_key"] for x in lu):
            continue
        line = team_line([x for x in lu if x.get("name")])
        pl = p_lowest(line)
        claims.append({"name": c["name"], "player_key": c["player_key"], "pos": c["pos"], "team": c["team"],
                       "proj": c["proj"], "floor_p25": c["floor_p25"], "sd": c["sd"], "risk_adj": c["risk_adj"],
                       "from_chopped_team": c["from_chopped_team"], "yahoo_status": c["yahoo_status"],
                       "replaces": [x["name"] for x in rec if x["player_key"] not in {y.get("player_key") for y in lu}],
                       "proj_sd": line, "p_lowest": pl, "cut_in_chop_risk": round(base_p - pl, 3)})
    claims.sort(key=lambda c: c["p_lowest"])
    chop_risk["single_claims"] = claims
    # best lineup if every claim landed, from the whole unrostered pool; fallbacks = other single claims at the
    # same position that also cut the chop risk by at least one point, best first
    ideal, _ = best_lineup(mine + [dict(m, slot="waiver") for m in pool])
    ideal_line = team_line([x for x in ideal if x.get("name")])
    in_ideal = {y.get("player_key") for y in ideal}
    by_key = {c["player_key"]: c for c in claims}
    picks = []
    for x in ideal:
        if x.get("current_slot") != "waiver":
            continue
        one = by_key.get(x["player_key"], {})
        alts = [c["name"] for c in claims if pos1(c["pos"]) == pos1(x["pos"]) and c["player_key"] not in in_ideal
                and c["cut_in_chop_risk"] >= 0.01][:4]
        picks.append({"slot": x["slot"], "claim": x["name"], "pos": x["pos"], "team": x["team"], "proj": x["proj"],
                      "risk_adj": x["risk_adj"], "floor_p25": x["floor_p25"], "sd": x["sd"],
                      "from_chopped_team": x.get("from_chopped_team"), "yahoo_status": x.get("yahoo_status"),
                      "replaces_alone": one.get("replaces"), "p_lowest_alone": one.get("p_lowest"),
                      "cut_in_chop_risk": one.get("cut_in_chop_risk"),
                      "verdict": "claim" if (one.get("cut_in_chop_risk") or 0) >= 0.01 else "skip: under 1 point of chop risk",
                      "fallbacks": alts})
    chop_risk["ideal"] = {"proj_sd": ideal_line, "p_lowest": p_lowest(ideal_line), "picks": picks,
                          "lineup": [(x["slot"], x.get("name"), x.get("current_slot")) for x in ideal]}

    my_wk = {}
    for wk in rundown:
        for t in wk["teams"]:
            if t["is_will"]:
                my_wk[wk["week"]] = {"rank": t["rank"], "of": t["of"], "pts": t["pts"], "proj": t["proj"],
                                     "margin_over_lowest": round(t["pts"] - wk["teams"][-1]["pts"], 2)
                                     if t["team_name"] != wk["lowest"] else 0.0}
    return {"league_id": g.LEAGUE_ID, "week": week, "season": season, "scoring": sc,
            "roster_slots": json.loads(conn.execute("SELECT roster_slots_json FROM league").fetchone()[0]),
            "rundown": rundown, "will_by_week": my_wk, "lineup": lineup, "roster": mine,
            "waiver_pool": pool[:25], "waiver_by_pos": {p: v[:6] for p, v in by_pos.items()},
            "waiver_chopped_team": [m for m in pool if m["from_chopped_team"]],
            "chop_risk": chop_risk, "chopped_last_week": chopped_team,
            "pos_avg_allowed": {k: round(v, 2) for k, v in pos_avg.items()},
            "recompute_check": recompute, "status_error": status.get("_error"),
            "standings_now": g.summary(conn), "method": __doc__,
            "sql": {"team week scores (rundown, chop check)": SQL_TEAM_WEEKS,
                    "player weekly history (mean, SD, floor, matchup)": SQL_PLAYER_WEEKS,
                    "roster with week projection (start/sit, chop risk)": SQL_ROSTER,
                    "waiver pool (claims)": SQL_POOL}}


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", type=int, default=None)
    a = ap.parse_args(argv)
    week = a.week or connect(g.DB, readonly=True).execute("SELECT MAX(week) FROM week_scores").fetchone()[0]
    json.dump(build(week), sys.stdout, indent=1, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
