"""Daily 07:00 job. If a game kicks off today (per `games`): pull Yahoo rosters for every
league team and projections for every rostered + top free-agent player. Else exit 0.

    python -m nfl.am [--force] [--week N]

A dead cookie fails only the projections step: rosters are still written, the run ends
ok=1 with error='cookie' so the row is visible.
"""
from __future__ import annotations

import argparse
import sys

from . import players, yahoo_web
from .common import Run, log, now_utc, retry, today_et, utc_to_et_date
from .db import init
from .yahoo_api import Yahoo


def games_today(conn, season: int):
    today = today_et()
    return [g for g in conn.execute("SELECT * FROM games WHERE season=?", (season,))
            if utc_to_et_date(g["kickoff_utc"]) == today]


def run(force: bool = False, week: int = None) -> int:
    conn = init()
    with Run(conn, "am") as r:
        y = Yahoo()
        lg = retry(y.league, what="yahoo league")
        season = lg["season"]
        todays = games_today(conn, season)
        if not todays and not force:
            log.info("no games today (%s); nothing to do", today_et())
            r.error = "no games"
            return 0
        week = week or (todays[0]["week"] if todays else lg["current_week"])
        game_key = lg["league_key"].split(".")[0]
        log.info("am: season %s week %s, %d games today", season, week, len(todays))

        # rosters
        teams = conn.execute("SELECT team_key, team_id FROM league_teams").fetchall()
        if not teams:
            raise RuntimeError("league_teams is empty; run `python -m nfl.weekly` first")
        seen, roster_rows = {}, []
        for t in teams:
            for p in retry(y.roster, t["team_key"], week, what=f"yahoo roster {t['team_id']}"):
                seen[p["player_key"]] = p
                roster_rows.append((season, week, t["team_key"], p["player_key"], p["slot"], now_utc()))
        players.upsert_yahoo(conn, list(seen.values()))
        conn.execute("DELETE FROM rosters WHERE season=? AND week=?", (season, week))
        conn.executemany(
            "INSERT INTO rosters (season, week, team_key, player_key, slot, pulled_at) VALUES (?,?,?,?,?,?)",
            roster_rows)
        conn.commit()
        r.rows += len(roster_rows)
        log.info("rosters: %d rows across %d teams", len(roster_rows), len(teams))

        # projections
        try:
            proj = retry(yahoo_web.projections, week, what="yahoo_web projections")
        except yahoo_web.CookieDead as e:
            log.error("projections skipped: %s", e)
            r.error = "cookie"
            return 0

        known = {row["yahoo_id"]: row["player_key"] for row in
                 conn.execute("SELECT yahoo_id, player_key FROM players")}
        missing = [f"{game_key}.p.{yid}" for yid in proj if yid not in known]
        if missing:
            fresh = retry(y.players, missing, what="yahoo player bios")
            players.upsert_yahoo(conn, fresh)
            known.update({p["yahoo_id"]: p["player_key"] for p in fresh})

        # opponent / game_id from the schedule via the player's team
        team_game = {}
        for g in conn.execute("SELECT game_id, home, away FROM games WHERE season=? AND week=?", (season, week)):
            team_game[g["home"]] = (g["away"], g["game_id"])
            team_game[g["away"]] = (g["home"], g["game_id"])
        pteam = {row["player_key"]: (row["team"] or "").upper() for row in
                 conn.execute("SELECT player_key, team FROM players")}
        alias = players.TEAM_ALIAS
        rows = []
        for yid, pts in proj.items():
            pk = known.get(yid)
            if not pk:
                continue
            tm = alias.get(pteam.get(pk, ""), pteam.get(pk, ""))
            opp, gid = team_game.get(tm, (None, None))
            rows.append((season, week, pk, pts, opp, gid, now_utc()))
        conn.executemany(
            "INSERT OR REPLACE INTO projections (season, week, player_key, proj_pts, opp, game_id, pulled_at, source) "
            "VALUES (?,?,?,?,?,?,?, 'yahoo_web')", rows)
        conn.commit()
        r.rows += len(rows)
        log.info("projections: %d rows", len(rows))
    return 0 if r.ok else 1


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="run even with no game today")
    ap.add_argument("--week", type=int, default=None)
    a = ap.parse_args(argv)
    return run(a.force, a.week)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
