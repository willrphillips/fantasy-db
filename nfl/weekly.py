"""Tuesday 05:00 job: nflverse schedules + rosters, Yahoo league meta/scoring/teams,
`players` map refresh. Unresolved-name report goes into `runs.error` with ok=1.

    python -m nfl.weekly [--season 2026]
"""
from __future__ import annotations

import argparse
import json
import sys

from . import nflverse, players
from .common import Run, log, now_utc, retry
from .db import init
from .yahoo_api import Yahoo


def run(season: int = None) -> int:
    conn = init()
    with Run(conn, "weekly") as r:
        y = Yahoo()
        lg = retry(y.league, what="yahoo league")
        season = season or lg["season"]

        games = retry(nflverse.schedules, season, what="nflverse schedules")
        r.rows += nflverse.upsert_games(conn, games)

        st = retry(y.settings, what="yahoo settings")
        conn.execute(
            "INSERT OR REPLACE INTO league (league_key, season, name, scoring_json, roster_slots_json, pulled_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (lg["league_key"], lg["season"], lg["name"], json.dumps(st["scoring"]),
             json.dumps(st["roster_slots"]), now_utc()))
        teams = retry(y.teams, what="yahoo teams")
        conn.executemany(
            "INSERT OR REPLACE INTO league_teams (team_key, league_key, team_id, team_name, manager, is_will) "
            "VALUES (:team_key, :league_key, :team_id, :team_name, :manager, :is_will)", teams)
        conn.commit()
        r.rows += 1 + len(teams)

        # every rostered player in the league, so the map covers what Edwin will ask about
        week = max(lg["current_week"], 1)
        seen = {}
        for t in teams:
            for p in retry(y.roster, t["team_key"], week, what=f"yahoo roster {t['team_id']}"):
                seen[p["player_key"]] = p
        # plus the top free agents, so waiver questions resolve too
        for p in retry(y.free_agents, count=100, what="yahoo free agents"):
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
