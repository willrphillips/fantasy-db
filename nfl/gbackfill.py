"""Mirror nfl.db's tables into guillotine.db for the guillotine league (Yahoo 1642654).

Same schema as nfl.db (`nfl.db.SCHEMA`), filled two ways:
  * league-scored data from Yahoo pages of league 1642654 (half-PPR, no K, no DEF):
    league, league_teams, rosters, projections, actuals, players (name/team/pos/bye)
  * league-independent NFL data copied from nfl.db, opened READ-ONLY: weeks, games, stats,
    and players.gsis_id (the Yahoo -> nflverse id map). nfl.db is never written.

    python -m nfl.gbackfill --weeks 1-12     # backfill
    python -m nfl.gbackfill --recent         # daily: last week + this week (launchd)

Rules as every NFL job: a `runs` row, retries inside, no Discord, no AI.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime

from .common import DB_PATH, ET, Run, log, now_utc, retry
from . import db as nfldb
from . import guillotine as g
from .yahoo_web import COOKIE_ERROR, CookieDead, YahooWeb

GROUPS = ("O",)          # the league has no K or DEF slots
DEPTH = {"O": 400}
LEAGUE_KEY = f"nfl.l.{g.LEAGUE_ID}"


def team_key(team_id: int) -> str:
    return f"{g.LEAGUE_ID}.t.{team_id}"


def open_db():
    conn = g.init()
    conn.executescript(nfldb.SCHEMA)
    conn.commit()
    return conn


def copy_nfl(conn) -> int:
    """weeks, games, stats and the gsis map from nfl.db, read-only."""
    src = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=10)
    n = 0
    for t in ("weeks", "games", "stats"):
        cols = [r[1] for r in src.execute(f"PRAGMA table_info({t})")]
        rows = src.execute(f"SELECT {','.join(cols)} FROM {t}").fetchall()
        conn.executemany(f"INSERT OR REPLACE INTO {t} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", rows)
        n += len(rows)
    rows = src.execute("SELECT player_key, yahoo_id, gsis_id, name, team, pos, bye_week, updated_at FROM players").fetchall()
    conn.executemany("INSERT OR IGNORE INTO players VALUES (?,?,?,?,?,?,?,?)", rows)
    conn.executemany("UPDATE players SET gsis_id=? WHERE player_key=? AND gsis_id IS NULL",
                     [(r[2], r[0]) for r in rows if r[2]])
    src.close()
    conn.commit()
    return n


def upsert_player(conn, p, ts):
    conn.execute(
        "INSERT INTO players (player_key, yahoo_id, name, team, pos, bye_week, updated_at) VALUES (?,?,?,?,?,?,?) "
        "ON CONFLICT(player_key) DO UPDATE SET name=excluded.name, team=COALESCE(excluded.team, team), "
        "pos=COALESCE(excluded.pos, pos), bye_week=COALESCE(excluded.bye_week, bye_week), updated_at=excluded.updated_at",
        (p["player_key"], p["yahoo_id"], p.get("name"), p.get("team"), p.get("pos"), p.get("bye_week"), ts))


def league_rows(conn, y) -> int:
    s = retry(y.settings, what="guillotine settings")
    conn.execute("INSERT OR REPLACE INTO league VALUES (?,?,?,?,?,?)",
                 (LEAGUE_KEY, datetime.now(ET).year, s["name"], json.dumps(s["scoring"]),
                  json.dumps(s["roster_slots"]), now_utc()))
    teams = conn.execute("SELECT team_id, team_name, is_will FROM teams").fetchall()
    for t in teams:
        conn.execute("INSERT OR REPLACE INTO league_teams VALUES (?,?,?,?,?,?)",
                     (team_key(t["team_id"]), LEAGUE_KEY, t["team_id"], t["team_name"], None, t["is_will"]))
    conn.commit()
    return 1 + len(teams)


def rosters(conn, y, r, week: int, season: int) -> int:
    """Every team alive in `week` (chopped in that week or later, or still alive)."""
    ts, n = now_utc(), 0
    teams = [t["team_id"] for t in conn.execute(
        "SELECT team_id FROM teams WHERE alive=1 OR chopped_week>=?", (week,))]
    for tid in teams:
        try:
            rows = retry(y.roster, tid, week, what=f"roster t{tid} w{week}", gap=30)
        except CookieDead:
            raise
        except Exception as e:  # noqa: BLE001
            r.note(f"roster t{tid} w{week} failed: {e}")
            continue
        conn.execute("DELETE FROM rosters WHERE season=? AND week=? AND team_key=?", (season, week, team_key(tid)))
        for p in rows:
            upsert_player(conn, p, ts)
            conn.execute("INSERT OR REPLACE INTO rosters VALUES (?,?,?,?,?,?)",
                         (season, week, team_key(tid), p["player_key"], p["slot"], ts))
            n += 1
        conn.commit()
    return n


def pool(conn, y, week: int, season: int, kind: str, is_final: int = 0) -> int:
    ts = now_utc()
    d = retry(y.players, week, kind, GROUPS, DEPTH, what=f"players {kind} w{week}", gap=30)
    n = 0
    for p in d.values():
        if p["pts"] is None:
            continue
        upsert_player(conn, p, ts)
        if kind == "proj":
            conn.execute("INSERT OR REPLACE INTO projections (season, week, player_key, proj_pts, pulled_at, source) "
                         "VALUES (?,?,?,?,?,'yahoo_web')", (season, week, p["player_key"], p["pts"], ts))
        else:
            conn.execute("INSERT OR REPLACE INTO actuals VALUES (?,?,?,?,?,?)",
                         (season, week, p["player_key"], p["pts"], ts, is_final))
        n += 1
    if kind == "act" and is_final:
        # the list is cut at DEPTH sorted by points; once final, an unlisted rostered or projected player scored 0
        cur = conn.execute(
            "INSERT OR IGNORE INTO actuals SELECT ?, ?, player_key, 0.0, ?, 1 FROM ("
            " SELECT player_key FROM projections WHERE season=? AND week=? "
            " UNION SELECT player_key FROM rosters WHERE season=? AND week=?)",
            (season, week, ts, season, week, season, week))
        n += cur.rowcount
    conn.commit()
    return n


def week_final(conn, season: int, week: int) -> int:
    ks = [r[0] for r in conn.execute("SELECT kickoff_utc FROM games WHERE season=? AND week=?", (season, week))]
    return int(bool(ks) and max(ks) < datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ") and
               (datetime.utcnow() - datetime.strptime(max(ks), "%Y-%m-%dT%H:%M:%SZ")).total_seconds() > 5 * 3600)


def run(weeks: list) -> int:
    conn = open_db()
    season = datetime.now(ET).year
    with Run(conn, "gbackfill") as r:
        try:
            y = YahooWeb(g.LEAGUE_ID)
            d = retry(g.fetch, what="guillotine league page")   # teams + chop weeks first
            g.store(conn, d)
            cur_week = d["week"]
            if weeks == ["recent"]:
                weeks = [w for w in (cur_week - 1, cur_week) if w >= 1]
            r.rows += copy_nfl(conn)
            r.rows += league_rows(conn, y)
            for w in weeks:
                r.rows += rosters(conn, y, r, w, season)
                r.rows += pool(conn, y, w, season, "proj")
                if w < cur_week or week_final(conn, season, w):
                    r.rows += pool(conn, y, w, season, "act", week_final(conn, season, w))
                elif w == cur_week:
                    r.rows += pool(conn, y, w, season, "act", 0)
                log.info("gbackfill week %s done, rows so far %s", w, r.rows)
            r.note(f"weeks {weeks[0]}-{weeks[-1]}")
        except CookieDead as e:
            r.fail(f"{COOKIE_ERROR}: guillotine backfill stopped ({e})")
    return 0 if r.ok else 1


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--weeks", help="e.g. 1-12 or 11")
    ap.add_argument("--recent", action="store_true", help="last week and this week")
    a = ap.parse_args(argv)
    if a.recent:
        return run(["recent"])
    lo, _, hi = (a.weeks or "1").partition("-")
    return run(list(range(int(lo), int(hi or lo) + 1)))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
