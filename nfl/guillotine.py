"""Will's guillotine league (Yahoo league 1642654, team 11): the lowest scorer each week is
chopped. One page, the league home, carries everything: survivors with this week's live and
projected points, and the eliminated teams with the week each was chopped.

Own db (`guillotine.db`, never nfl.db), own launchd job every 15 minutes, read by
`/guillotine` on nfl-serve. Same rules as the NFL jobs: a `runs` row every run, 3 retries,
no Discord, no AI.

    python -m nfl.guillotine           # pull and store
    python -m nfl.guillotine --show    # print the stored summary, no fetch
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from .common import ET, REPO, Run, log, now_utc, retry
from .db import connect
from . import yhtml
from .yahoo_web import COOKIE_ERROR, CookieDead, YahooWeb, _dump

LEAGUE_ID = int(os.environ.get("GUILLOTINE_LEAGUE_ID", "1642654"))
WILL_TEAM_ID = int(os.environ.get("GUILLOTINE_TEAM_ID", "11"))
DB = Path(os.environ.get("GUILLOTINE_DB") or REPO / "guillotine.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
  team_id INTEGER PRIMARY KEY, team_name TEXT, alive INTEGER, chopped_week INTEGER,
  season_pts REAL, fab TEXT, moves INTEGER, pts_per_week REAL, is_will INTEGER, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS week_scores (
  season INTEGER, week INTEGER, team_id INTEGER,
  rank INTEGER, pts REAL, proj_pts REAL, pulled_at TEXT,
  PRIMARY KEY (season, week, team_id)
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, job TEXT, started_at TEXT, ended_at TEXT,
  ok INTEGER, rows INTEGER, error TEXT
);
"""

_week = re.compile(r"Week\s+(\d+)\s+Matchup")
_th = re.compile(r"<th\b[^>]*>(.*?)</th>", re.S)
_tr = re.compile(r'<tr\b[^>]*data-target="/f1/\d+/(\d+)"[^>]*>(.*?)</tr>', re.S)
_td = re.compile(r"<td\b[^>]*>(.*?)</td>", re.S)
_num = re.compile(r"-?\d+(?:\.\d+)?")


def _f(t):
    m = _num.search((t or "").replace(",", ""))
    return float(m.group(0)) if m else None


def _table(body: str, tid: str) -> list:
    """Rows of one guillotine table as dicts keyed by header text, plus team_id from the
    row's data-target link (the parser in yhtml drops <tr> attributes)."""
    i = body.find(f'id="{tid}"')
    if i < 0:
        return None
    seg = body[i:body.find("</table>", i)]
    heads = [yhtml.text_of(h) for h in _th.findall(seg)]
    out = []
    for team_id, row in _tr.findall(seg):
        cells = [yhtml.text_of(c) for c in _td.findall(row)]
        d = dict(zip(heads, cells))
        d["team_id"] = int(team_id)
        out.append(d)
    return out


def parse(body: str) -> dict:
    alive = _table(body, "yfa-guillotine-standings-table")
    chopped = _table(body, "yfa-guillotine-chopped-standings-table")
    if alive is None or not alive:
        _dump(body, "no yfa-guillotine-standings-table rows")
        raise RuntimeError("guillotine standings table not found")
    m = _week.search(body)
    return {"week": int(m.group(1)) if m else None, "alive": alive, "chopped": chopped or []}


def fetch() -> dict:
    return parse(YahooWeb(LEAGUE_ID).get(""))


def init():
    conn = connect(DB)
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def store(conn, d: dict) -> int:
    ts, season = now_utc(), datetime.now(ET).year
    n = 0
    for t in d["alive"]:
        conn.execute(
            "INSERT OR REPLACE INTO teams VALUES (?,?,?,?,?,?,?,?,?,?)",
            (t["team_id"], t.get("Team"), 1, None, _f(t.get("Season Points")), t.get("FAB"),
             _f(t.get("Moves")), _f(t.get("Pts Per Week")), int(t["team_id"] == WILL_TEAM_ID), ts))
        if d["week"]:
            conn.execute(
                "INSERT OR REPLACE INTO week_scores VALUES (?,?,?,?,?,?,?)",
                (season, d["week"], t["team_id"], _f(t.get("Rank")), _f(t.get("Points")),
                 _f(t.get("Projected Points")), ts))
        n += 1
    for t in d["chopped"]:
        conn.execute(
            "INSERT OR REPLACE INTO teams VALUES (?,?,?,?,?,?,?,?,?,?)",
            (t["team_id"], t.get("Team"), 0, _f(t.get("Week")), _f(t.get("Season Points")),
             t.get("FAB"), _f(t.get("Moves")), _f(t.get("Pts Per Week")),
             int(t["team_id"] == WILL_TEAM_ID), ts))
        n += 1
    conn.commit()
    return n


def summary(conn) -> dict:
    """What Edwin needs: survivors this week, Will's margin over the lowest, the chop list."""
    wk = conn.execute("SELECT MAX(season), MAX(week) FROM week_scores").fetchone()
    season, week = wk[0], wk[1]
    rows = [dict(r) for r in conn.execute(
        "SELECT t.team_id, t.team_name, t.is_will, w.pts, w.proj_pts, w.rank, t.season_pts, "
        "t.pts_per_week, t.fab, w.pulled_at FROM teams t JOIN week_scores w USING (team_id) "
        "WHERE t.alive=1 AND w.season=? AND w.week=? ORDER BY w.proj_pts DESC", (season, week))]
    chopped = [dict(r) for r in conn.execute(
        "SELECT chopped_week AS week, team_id, team_name, season_pts, pts_per_week FROM teams "
        "WHERE alive=0 ORDER BY chopped_week DESC")]
    last = conn.execute("SELECT started_at, ok, rows, substr(error,1,300) AS error FROM runs "
                        "ORDER BY id DESC LIMIT 1").fetchone()
    last_ok = conn.execute("SELECT MAX(started_at) FROM runs WHERE ok=1").fetchone()[0]
    will = next((r for r in rows if r["is_will"]), None)
    out = {"league_id": LEAGUE_ID, "will_team_id": WILL_TEAM_ID, "season": season, "week": week,
           "will_alive": will is not None, "alive": rows, "chopped": chopped,
           "data_as_of": rows[0]["pulled_at"] if rows else None,
           "last_run": dict(last) if last else None, "last_ok_run": last_ok,
           "note": ("pts = points scored so far this week (live); proj_pts = Yahoo projection "
                    "for the week. The lowest pts when the week ends is chopped.")}
    if will:
        others = [r for r in rows if not r["is_will"]]
        if others:
            low_proj = min(others, key=lambda r: r["proj_pts"] or 0)
            low_live = min(others, key=lambda r: r["pts"] or 0)
            out["will"] = will
            out["lowest_other_by_proj"] = {"team_name": low_proj["team_name"], "proj_pts": low_proj["proj_pts"]}
            out["will_margin_proj"] = round((will["proj_pts"] or 0) - (low_proj["proj_pts"] or 0), 2)
            out["lowest_other_by_live"] = {"team_name": low_live["team_name"], "pts": low_live["pts"]}
            out["will_margin_live"] = round((will["pts"] or 0) - (low_live["pts"] or 0), 2)
            m = out["will_margin_proj"]
            out["danger"] = ("projected to be chopped" if m < 0 else
                             "close: within 10 projected points" if m < 10 else
                             "comfortable: 10+ projected points clear")
    return out


def run() -> int:
    conn = init()
    with Run(conn, "guillotine") as r:
        try:
            d = retry(fetch, what="guillotine league page")
        except CookieDead as e:
            r.fail(f"{COOKIE_ERROR}: guillotine page NOT loaded ({e})")
            return 1
        r.rows = store(conn, d)
        r.note(f"week {d['week']}: {len(d['alive'])} alive, {len(d['chopped'])} chopped")
        if not d["week"]:
            r.fail("current week not found on the page")
    return 0 if r.ok else 1


def main(argv):
    if argv and argv[0] == "--show":
        print(json.dumps(summary(init()), indent=2, default=str))
        return 0
    return run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
