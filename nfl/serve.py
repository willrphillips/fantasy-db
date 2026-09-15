"""Read-only HTTP API over nfl.db for Edwin. Stdlib only. Binds to the Tailscale address,
never 0.0.0.0. Every endpoint except /health needs `Authorization: Bearer <key>`, key in
`.secrets/serve-key`.

    python -m nfl.serve                # NFL_SERVE_HOST / NFL_SERVE_PORT / NFL_SERVE_LOG override

Endpoints (GET, JSON):
    /health                       liveness, no key
    /status                       last run per job, row counts, latest week
    /player?name=X[&week=N]       fuzzy on players.name; all matches if ambiguous
    /roster?team=K[&week=N]       one team's roster with proj + actual (team = team_id)
    /week?week=N[&pos=WR]         proj vs actual for the week, whole league
    /deltas?pos=RB&n=20           biggest over/under-performers season to date
    /fa?pos=TE&n=15[&week=N]      projected players not on any roster this week
    /sql?q=SELECT ...             SELECT only, 5 s timeout, 500-row cap
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .common import DB_PATH, read_secret_text
from .db import connect

HOST = os.environ.get("NFL_SERVE_HOST", "100.126.114.42")
PORT = int(os.environ.get("NFL_SERVE_PORT", "8094"))
LOG = Path(os.environ.get("NFL_SERVE_LOG") or Path.home() / "Library" / "Logs" / "nfl-serve.log")
SQL_TIMEOUT_S = 5
ROW_CAP = 500

log = logging.getLogger("nfl.serve")


def _key() -> str:
    try:
        return read_secret_text("serve-key")
    except FileNotFoundError:
        return ""


def _rows(cur) -> list:
    cols = [d[0] for d in cur.description] if cur.description else []
    return [dict(zip(cols, r)) for r in cur.fetchmany(ROW_CAP)]


def _latest_week(conn):
    r = conn.execute("SELECT MAX(week) FROM projections").fetchone()
    return r[0] if r and r[0] else conn.execute("SELECT MAX(week) FROM rosters").fetchone()[0]


# ------------------------------------------------------------------ queries

def q_status(conn, p):
    jobs = _rows(conn.execute(
        "SELECT job, started_at, ended_at, ok, rows, substr(error,1,200) AS error FROM runs r "
        "WHERE id = (SELECT MAX(id) FROM runs WHERE job = r.job) ORDER BY job"))
    counts = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
              for t in ("games", "players", "rosters", "projections", "actuals", "stats", "runs")}
    return {"db": str(DB_PATH), "latest_week": _latest_week(conn), "last_runs": jobs, "counts": counts}


def q_player(conn, p):
    name = (p.get("name") or [""])[0].strip()
    if not name:
        return {"error": "name required"}, 400
    week = (p.get("week") or [None])[0]
    like = "%" + "%".join(name.split()) + "%"
    players = _rows(conn.execute(
        "SELECT player_key, name, team, pos, bye_week, gsis_id FROM players WHERE name LIKE ? "
        "ORDER BY name LIMIT 20", (like,)))
    for pl in players:
        sql = ("SELECT week, proj_pts, act_pts, delta, is_final, rostered_by, slot FROM player_week "
               "WHERE player_key=?")
        args = [pl["player_key"]]
        if week:
            sql += " AND week=?"
            args.append(int(week))
        pl["weeks"] = _rows(conn.execute(sql + " ORDER BY week", args))
        if pl["gsis_id"]:
            pl["stats"] = _rows(conn.execute(
                "SELECT * FROM stats WHERE gsis_id=?" + (" AND week=?" if week else "") + " ORDER BY week",
                [pl["gsis_id"]] + ([int(week)] if week else [])))
    return {"query": name, "matches": players}


def q_roster(conn, p):
    team = int((p.get("team") or ["1"])[0])
    week = (p.get("week") or [None])[0]
    week = int(week) if week else _latest_week(conn)
    t = conn.execute("SELECT team_key, team_name, manager FROM league_teams WHERE team_id=?", (team,)).fetchone()
    if not t:
        return {"error": f"no team_id {team}"}, 404
    rows = _rows(conn.execute(
        "SELECT r.slot, pl.player_key, pl.name, pl.team, pl.pos, pl.bye_week, pj.proj_pts, a.act_pts, "
        "a.act_pts - pj.proj_pts AS delta, a.is_final FROM rosters r "
        "JOIN players pl USING (player_key) "
        "LEFT JOIN projections pj ON pj.player_key=r.player_key AND pj.season=r.season AND pj.week=r.week "
        "LEFT JOIN actuals a ON a.player_key=r.player_key AND a.season=r.season AND a.week=r.week "
        "WHERE r.team_key=? AND r.week=? ORDER BY CASE r.slot WHEN 'BN' THEN 1 WHEN 'IR' THEN 2 ELSE 0 END, r.slot",
        (t["team_key"], week)))
    starters = [r for r in rows if r["slot"] not in ("BN", "IR")]
    return {"team_id": team, "team": dict(t), "week": week, "players": rows,
            "starters_proj": round(sum(r["proj_pts"] or 0 for r in starters), 2),
            "starters_act": round(sum(r["act_pts"] or 0 for r in starters), 2)}


def q_week(conn, p):
    week = (p.get("week") or [None])[0]
    week = int(week) if week else _latest_week(conn)
    pos = (p.get("pos") or [None])[0]
    sql = "SELECT * FROM player_week WHERE week=?"
    args = [week]
    if pos:
        sql += " AND pos LIKE ?"
        args.append(f"%{pos.upper()}%")
    return {"week": week, "players": _rows(conn.execute(sql + " ORDER BY proj_pts DESC", args))}


def q_deltas(conn, p):
    pos = (p.get("pos") or [None])[0]
    n = min(int((p.get("n") or ["20"])[0]), ROW_CAP)
    sql = ("SELECT player_key, name, team, pos, COUNT(*) AS weeks, ROUND(SUM(proj_pts),2) AS proj, "
           "ROUND(SUM(act_pts),2) AS act, ROUND(SUM(delta),2) AS delta FROM player_week WHERE act_pts IS NOT NULL")
    args = []
    if pos:
        sql += " AND pos LIKE ?"
        args.append(f"%{pos.upper()}%")
    sql += " GROUP BY player_key"
    over = _rows(conn.execute(sql + " ORDER BY delta DESC LIMIT ?", args + [n]))
    under = _rows(conn.execute(sql + " ORDER BY delta ASC LIMIT ?", args + [n]))
    return {"pos": pos, "over": over, "under": under}


def q_fa(conn, p):
    pos = (p.get("pos") or [None])[0]
    n = min(int((p.get("n") or ["15"])[0]), ROW_CAP)
    week = (p.get("week") or [None])[0]
    week = int(week) if week else _latest_week(conn)
    sql = "SELECT * FROM player_week WHERE week=? AND rostered_by IS NULL"
    args = [week]
    if pos:
        sql += " AND pos LIKE ?"
        args.append(f"%{pos.upper()}%")
    return {"week": week, "pos": pos,
            "players": _rows(conn.execute(sql + " ORDER BY proj_pts DESC LIMIT ?", args + [n]))}


def q_sql(conn, p):
    q = (p.get("q") or [""])[0].strip().rstrip(";")
    if not re.match(r"^\s*(select|with)\b", q, re.I) or ";" in q:
        return {"error": "SELECT only"}, 400
    deadline = time.monotonic() + SQL_TIMEOUT_S

    def guard():
        return 1 if time.monotonic() > deadline else 0

    conn.set_progress_handler(guard, 10000)
    try:
        return {"rows": _rows(conn.execute(q))}
    except sqlite3.OperationalError as e:
        if "interrupted" in str(e):
            return {"error": f"query exceeded {SQL_TIMEOUT_S}s"}, 408
        return {"error": str(e)}, 400
    finally:
        conn.set_progress_handler(None, 0)


ROUTES = {"/status": q_status, "/player": q_player, "/roster": q_roster, "/week": q_week,
          "/deltas": q_deltas, "/fa": q_fa, "/sql": q_sql}


# ------------------------------------------------------------------ server

class Handler(BaseHTTPRequestHandler):
    server_version = "nfl-serve/1"

    def _send(self, code: int, body):
        data = json.dumps(body, default=str).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        log.info("%s %s", self.client_address[0], fmt % args)

    def do_GET(self):
        u = urlparse(self.path)
        if u.path == "/health":
            ok = DB_PATH.exists()
            return self._send(200 if ok else 503, {"ok": ok, "db": str(DB_PATH)})
        fn = ROUTES.get(u.path)
        if not fn:
            return self._send(404, {"error": "no such endpoint", "endpoints": ["/health"] + list(ROUTES)})
        key = _key()
        auth = self.headers.get("Authorization", "")
        if not key or auth != f"Bearer {key}":
            return self._send(401, {"error": "bearer key required"})
        try:
            conn = connect(readonly=True)
        except sqlite3.OperationalError as e:
            return self._send(503, {"error": f"db unavailable: {e}"})
        try:
            out = fn(conn, parse_qs(u.query))
            if isinstance(out, tuple):
                return self._send(out[1], out[0])
            return self._send(200, out)
        except Exception as e:  # noqa: BLE001
            log.exception("handler error")
            return self._send(500, {"error": str(e)})
        finally:
            conn.close()


def main(argv=None):
    LOG.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(LOG)])  # the nfl logger already echoes to stderr
    if HOST == "0.0.0.0":
        raise SystemExit("refusing to bind 0.0.0.0; set NFL_SERVE_HOST to the Tailscale address")
    if not _key():
        log.warning("no .secrets/serve-key; every keyed endpoint will answer 401")
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    log.info("nfl-serve listening on http://%s:%d db=%s", HOST, PORT, DB_PATH)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
