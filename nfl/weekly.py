"""Tuesday 05:00 job: nflverse schedules + rosters, Yahoo league meta/scoring/teams
(from the website), `players` map refresh. Unresolved-name report goes into `runs.error`
with ok=1.

    python -m nfl.weekly [--season 2026]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime

from . import nflverse, players
from .common import ET, LEAGUE_ID, Run, WILL_TEAM_ID, log, now_utc, retry
from .db import init
from .yahoo_web import YahooWeb

LEAGUE_KEY = f"l.{LEAGUE_ID}"


def team_key(team_id: int) -> str:
    return f"t.{int(team_id)}"


def run(season: int = None) -> int:
    conn = init()
    with Run(conn, "weekly") as r:
        season = season or datetime.now(ET).year
        y = YahooWeb()

        games = retry(nflverse.schedules, season, what="nflverse schedules")
        r.rows += nflverse.upsert_games(conn, games)

        st = retry(y.standings, what="yahoo standings")
        cfg = retry(y.settings, what="yahoo settings")
        conn.execute(
            "INSERT OR REPLACE INTO league (league_key, season, name, scoring_json, roster_slots_json, pulled_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (LEAGUE_KEY, season, cfg["name"], json.dumps(cfg["scoring"]),
             json.dumps(cfg["roster_slots"]), now_utc()))
        conn.executemany(
            "INSERT OR REPLACE INTO league_teams (team_key, league_key, team_id, team_name, manager, is_will) "
            "VALUES (?, ?, ?, ?, NULL, ?)",
            [(team_key(t["team_id"]), LEAGUE_KEY, t["team_id"], t["team_name"],
              1 if t["team_id"] == WILL_TEAM_ID else 0) for t in st["teams"]])
        conn.commit()
        r.rows += 1 + len(st["teams"])
        week = st["current_week"] or 1
        log.info("league %r: %d teams, current week %s", cfg["name"], len(st["teams"]), week)

        # every rostered player plus the projected pool, so the map covers what Edwin asks about
        seen = {}
        for t in st["teams"]:
            for p in retry(y.roster, t["team_id"], week, what=f"yahoo roster {t['team_id']}"):
                seen[p["player_key"]] = p
        for p in retry(y.players, week, "proj", what="yahoo players").values():
            seen.setdefault(p["player_key"], p)
        r.rows += players.upsert_yahoo(conn, list(seen.values()))

        nfl_ro = retry(nflverse.rosters, season, what="nflverse rosters")
        m = players.map_gsis(conn, nfl_ro)
        log.info("players map: %d by yahoo_id, %d by name, %d unresolved",
                 m["by_yahoo_id"], m["by_name"], len(m["unresolved"]))
        if m["unresolved"]:
            r.error = "unresolved gsis_id: " + "; ".join(m["unresolved"])[:4000]
    return 0 if r.ok else 1


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=None)
    a = ap.parse_args(argv)
    return run(a.season)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
