"""Daily 02:00 job. If a game finished yesterday: refresh `games` scores from nflverse,
pull Yahoo actual points for the week, pull nflverse player_stats for the week. Mark
`actuals.is_final=1` once every game in the week is final. Else exit 0.

    python -m nfl.pm [--force] [--week N]
"""
from __future__ import annotations

import argparse
import sys
from datetime import timedelta

from . import nflverse
from .common import Run, log, now_utc, retry, today_et, utc_to_et_date
from .db import init
from .yahoo_api import Yahoo


def games_yesterday(conn, season: int):
    y = today_et() - timedelta(days=1)
    return [g for g in conn.execute("SELECT * FROM games WHERE season=?", (season,))
            if utc_to_et_date(g["kickoff_utc"]) == y]


def run(force: bool = False, week: int = None) -> int:
    conn = init()
    with Run(conn, "pm") as r:
        y = Yahoo()
        lg = retry(y.league, what="yahoo league")
        season = lg["season"]
        yest = games_yesterday(conn, season)
        if not yest and not force and week is None:
            log.info("no games yesterday (%s); nothing to do", today_et() - timedelta(days=1))
            r.error = "no games"
            return 0
        week = week or (yest[0]["week"] if yest else lg["current_week"])
        log.info("pm: season %s week %s, %d games yesterday", season, week, len(yest))

        # scores + status
        games = retry(nflverse.schedules, season, what="nflverse schedules")
        nflverse.upsert_games(conn, games)
        wk = [g for g in games if g["week"] == week]
        is_final = 1 if wk and all(g["status"] == "final" for g in wk) else 0

        # who to score: everyone rostered or projected this week
        keys = [row[0] for row in conn.execute(
            "SELECT player_key FROM rosters WHERE season=? AND week=? "
            "UNION SELECT player_key FROM projections WHERE season=? AND week=?",
            (season, week, season, week))]
        if not keys:
            raise RuntimeError(f"no rosters/projections for week {week}; run am first")
        pts = retry(y.week_points, keys, week, what="yahoo week points")
        rows = [(season, week, pk, v, now_utc(), is_final) for pk, v in pts.items() if v is not None]
        conn.executemany(
            "INSERT OR REPLACE INTO actuals (season, week, player_key, act_pts, pulled_at, is_final) "
            "VALUES (?,?,?,?,?,?)", rows)
        conn.commit()
        r.rows += len(rows)
        log.info("actuals: %d rows, is_final=%d", len(rows), is_final)

        stats = retry(nflverse.player_stats, season, week, what="nflverse player_stats")
        r.rows += nflverse.upsert_stats(conn, stats)
        if not stats:
            r.error = f"nflverse has no week {week} stats yet"
    return 0 if r.ok else 1


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="run even with no game yesterday")
    ap.add_argument("--week", type=int, default=None)
    a = ap.parse_args(argv)
    return run(a.force, a.week)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
