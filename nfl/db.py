"""nfl.db: schema and connection. `python -m nfl.db init` is idempotent.

Schema is the one in NFL_PLAN.md. Change it there first.
"""
from __future__ import annotations

import sqlite3
import sys

from .common import DB_PATH, log

SCHEMA = """
CREATE TABLE IF NOT EXISTS weeks (
  season INTEGER, week INTEGER,
  first_kickoff_utc TEXT, last_kickoff_utc TEXT,
  PRIMARY KEY (season, week)
);

CREATE TABLE IF NOT EXISTS games (
  game_id TEXT PRIMARY KEY, season INTEGER, week INTEGER,
  kickoff_utc TEXT, home TEXT, away TEXT,
  home_score INTEGER, away_score INTEGER, status TEXT
);

CREATE TABLE IF NOT EXISTS players (
  player_key TEXT PRIMARY KEY,
  yahoo_id INTEGER, gsis_id TEXT,
  name TEXT, team TEXT, pos TEXT, bye_week INTEGER,
  updated_at TEXT
);

CREATE TABLE IF NOT EXISTS league (
  league_key TEXT PRIMARY KEY, season INTEGER, name TEXT,
  scoring_json TEXT, roster_slots_json TEXT, pulled_at TEXT
);

CREATE TABLE IF NOT EXISTS league_teams (
  team_key TEXT PRIMARY KEY, league_key TEXT, team_id INTEGER,
  team_name TEXT, manager TEXT, is_will INTEGER
);

CREATE TABLE IF NOT EXISTS rosters (
  season INTEGER, week INTEGER, team_key TEXT, player_key TEXT,
  slot TEXT,
  pulled_at TEXT,
  PRIMARY KEY (season, week, team_key, player_key)
);

CREATE TABLE IF NOT EXISTS projections (
  season INTEGER, week INTEGER, player_key TEXT,
  proj_pts REAL, opp TEXT, game_id TEXT,
  pulled_at TEXT, source TEXT DEFAULT 'yahoo_web',
  PRIMARY KEY (season, week, player_key)
);

CREATE TABLE IF NOT EXISTS actuals (
  season INTEGER, week INTEGER, player_key TEXT,
  act_pts REAL, pulled_at TEXT, is_final INTEGER,
  PRIMARY KEY (season, week, player_key)
);

CREATE TABLE IF NOT EXISTS stats (
  season INTEGER, week INTEGER, gsis_id TEXT,
  pass_att INTEGER, pass_cmp INTEGER, pass_yds INTEGER, pass_td INTEGER, pass_int INTEGER,
  rush_att INTEGER, rush_yds INTEGER, rush_td INTEGER,
  tgt INTEGER, rec INTEGER, rec_yds INTEGER, rec_td INTEGER,
  fum_lost INTEGER, two_pt INTEGER,
  fg_made INTEGER, fg_att INTEGER, xp_made INTEGER,
  snaps INTEGER, pulled_at TEXT,
  PRIMARY KEY (season, week, gsis_id)
);

CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, job TEXT, started_at TEXT, ended_at TEXT,
  ok INTEGER, rows INTEGER, error TEXT
);

CREATE VIEW IF NOT EXISTS player_week AS
SELECT p.season, p.week, pl.player_key, pl.name, pl.team, pl.pos,
       p.proj_pts, a.act_pts, a.act_pts - p.proj_pts AS delta,
       a.is_final, r.team_key AS rostered_by, r.slot
FROM projections p
JOIN players pl USING (player_key)
LEFT JOIN actuals a USING (season, week, player_key)
LEFT JOIN rosters r USING (season, week, player_key);
"""

TABLES = ["weeks", "games", "players", "league", "league_teams", "rosters",
          "projections", "actuals", "stats", "runs"]


def connect(path=None, readonly: bool = False) -> sqlite3.Connection:
    path = str(path or DB_PATH)
    if readonly:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
    else:
        conn = sqlite3.connect(path, timeout=30)
        conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def init(path=None) -> sqlite3.Connection:
    conn = connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def tables(conn) -> list:
    q = ("SELECT name FROM sqlite_master WHERE type IN ('table','view') "
         "AND name NOT LIKE 'sqlite_%' ORDER BY name")
    return [r[0] for r in conn.execute(q)]


def main(argv):
    if not argv or argv[0] != "init":
        print("usage: python -m nfl.db init", file=sys.stderr)
        return 2
    conn = init()
    names = tables(conn)
    missing = [t for t in TABLES + ["player_week"] if t not in names]
    print(f"{DB_PATH}: {len(names)} objects: {' '.join(names)}")
    if missing:
        print(f"MISSING: {missing}", file=sys.stderr)
        return 1
    log.info("schema ok")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
