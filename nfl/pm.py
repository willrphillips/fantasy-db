"""Daily 02:00 job. If a game finished yesterday: refresh `games` scores from nflverse,
pull actual points for the week from the Yahoo players list (S_W_N), pull nflverse
player_stats for the week. Mark `actuals.is_final=1` once every game in the week is
final. Else exit 0.

    python -m nfl.pm [--force] [--week N]
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from . import nflverse
from .common import ET, Run, log, now_utc, retry, today_et, utc_to_et_date
from .db import init
from .yahoo_web import CookieDead, YahooWeb


def games_yesterday(conn, season: int):
    y = today_et() - timedelta(days=1)
    return [g for g in conn.execute("SELECT * FROM games WHERE season=?", (season,))
            if utc_to_et_date(g["kickoff_utc"]) == y]


def week_is_final(games: list) -> int:
    """nflverse says final, or every kickoff is more than 5 hours in the past."""
    if not games:
        return 0
    if all(g["status"] == "final" for g in games):
        return 1
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return 1 if all(g["kickoff_utc"] < cutoff for g in games) else 0


def run(force: bool = False, week: int = None) -> int:
    conn = init()
    with Run(conn, "pm") as r:
        season = datetime.now(ET).year
        yest = games_yesterday(conn, season)
        if not yest and not force and week is None:
            log.info("no games yesterday (%s); nothing to do", today_et() - timedelta(days=1))
            r.error = "no games"
            return 0
        games = retry(nflverse.schedules, season, what="nflverse schedules")
        nflverse.upsert_games(conn, games)
        if week is None:
            week = yest[0]["week"] if yest else retry(YahooWeb().current_week, what="yahoo week")
        wk = [g for g in games if g["week"] == week]
        is_final = week_is_final(wk)
        log.info("pm: season %s week %s, %d games yesterday, is_final=%d", season, week, len(yest), is_final)

        try:
            pool = retry(YahooWeb().players, week, "act", what="yahoo players act")
        except CookieDead as e:
            r.error = "cookie"
            raise RuntimeError(str(e))
        rows = [(season, week, p["player_key"], p["pts"], now_utc(), is_final)
                for p in pool.values() if p["pts"] is not None]
        conn.executemany(
            "INSERT OR REPLACE INTO actuals (season, week, player_key, act_pts, pulled_at, is_final) "
            "VALUES (?,?,?,?,?,?)", rows)
        conn.commit()
        r.rows += len(rows)
        log.info("actuals: %d rows", len(rows))

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
