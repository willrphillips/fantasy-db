"""Daily 07:00 job. If a game kicks off today (per `games`): pull every league roster for
the week and projected points for every rostered + listed player. Else exit 0.

    python -m nfl.am [--force] [--week N]

Rosters come from the eight team pages (slot + proj), the free-agent pool from the
players list. A dead cookie fails the whole run (ok=0, error='cookie'); there is no
partial source any more.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime

from . import players
from .common import ET, Run, log, now_utc, retry, today_et, utc_to_et_date
from .db import init
from .weekly import team_key
from .yahoo_web import CookieDead, YahooWeb


def games_today(conn, season: int):
    today = today_et()
    return [g for g in conn.execute("SELECT * FROM games WHERE season=?", (season,))
            if utc_to_et_date(g["kickoff_utc"]) == today]


def team_game_map(conn, season: int, week: int) -> dict:
    m = {}
    for g in conn.execute("SELECT game_id, home, away FROM games WHERE season=? AND week=?", (season, week)):
        m[g["home"]] = (g["away"], g["game_id"])
        m[g["away"]] = (g["home"], g["game_id"])
    return m


def run(force: bool = False, week: int = None) -> int:
    conn = init()
    with Run(conn, "am") as r:
        season = datetime.now(ET).year
        todays = games_today(conn, season)
        if not todays and not force and week is None:
            log.info("no games today (%s); nothing to do", today_et())
            r.error = "no games"
            return 0
        teams = conn.execute("SELECT team_key, team_id FROM league_teams").fetchall()
        if not teams:
            raise RuntimeError("league_teams is empty; run `python -m nfl.weekly` first")
        try:
            y = YahooWeb()
            week = week or (todays[0]["week"] if todays else retry(y.current_week, what="yahoo week"))
            log.info("am: season %s week %s, %d games today", season, week, len(todays))

            seen, roster_rows, proj = {}, [], {}
            for t in teams:
                for p in retry(y.roster, t["team_id"], week, what=f"yahoo roster {t['team_id']}"):
                    seen[p["player_key"]] = p
                    roster_rows.append((season, week, team_key(t["team_id"]), p["player_key"], p["slot"], now_utc()))
                    if p["proj_pts"] is not None:
                        proj[p["player_key"]] = p["proj_pts"]
            pool = retry(y.players, week, "proj", what="yahoo players proj")
            for p in pool.values():
                seen.setdefault(p["player_key"], p)
                if p["pts"] is not None:
                    proj.setdefault(p["player_key"], p["pts"])
        except CookieDead as e:
            r.error = "cookie"
            raise RuntimeError(str(e))

        players.upsert_yahoo(conn, list(seen.values()))
        conn.execute("DELETE FROM rosters WHERE season=? AND week=?", (season, week))
        conn.executemany(
            "INSERT INTO rosters (season, week, team_key, player_key, slot, pulled_at) VALUES (?,?,?,?,?,?)",
            roster_rows)
        r.rows += len(roster_rows)
        log.info("rosters: %d rows across %d teams", len(roster_rows), len(teams))

        tg = team_game_map(conn, season, week)
        alias = players.TEAM_ALIAS
        rows = []
        for pk, pts in proj.items():
            tm = (seen[pk].get("team") or "").upper()
            opp, gid = tg.get(alias.get(tm, tm), (None, None))
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
