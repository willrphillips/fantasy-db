"""The Yahoo read path, from the website with Will's login cookie.

Yahoo gated the Fantasy Sports API behind an approval queue in 2026 (verified 2026-09-15:
sports.yahoo.com/developer/access; even approved access is read-only). So every league
fact comes from the HTML pages below, parsed with `yhtml`. `yahoo_api.py` stays on disk,
retired, in case the application is approved.

Secret: `.secrets/yahoo_cookie.txt`, the raw `Cookie:` header from a logged-in browser
request to football.fantasysports.yahoo.com. A dead cookie raises CookieDead.

Pages (all under https://football.fantasysports.yahoo.com/f1/{league}):
  /                          standings table: team_id + name for every team; current week
  /settings                  league name, scoring modifiers, roster positions
  /{team}/team?week=N        one roster for one week: slot, player, bye, actual, projected
  /players?status=ALL&pos=P&stat1=S_PW_N   every player, projected points for week N
  /players?status=ALL&pos=P&stat1=S_W_N    every player, actual points for week N

player_key is `p.{yahoo_id}`; the API game-key prefix is not on the site and not needed.
"""
from __future__ import annotations

import re
import sys
import time

import requests

from .common import LEAGUE_ID, REPO, log, read_secret_text
from . import yhtml

COOKIE_FILE = "yahoo_cookie.txt"
BASE = f"https://football.fantasysports.yahoo.com/f1/{LEAGUE_ID}"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
PAGE = 25
# players-list depth per position group: rostered players in an 8-team league plus a
# free-agent tail deep enough for waiver questions
DEPTH = {"O": 300, "K": 32, "DEF": 32}
PAUSE_S = 1.0  # between page fetches; be a polite browser

_pid = re.compile(r'data-ys-playerid="(\d+)"')
_name = re.compile(r'<a[^>]*class="[^"]*\bname\b[^"]*"[^>]*title="([^"]*)"')
_teampos = re.compile(r'<span class="Fz-xxs">([^<]*)</span>')
_status = re.compile(r'class="ysf-game-status[^"]*"[^>]*>(.*?)</div>', re.S)
_num = re.compile(r"-?\d+(?:\.\d+)?")
_team_link = re.compile(rf'href="/f1/{LEAGUE_ID}/(\d+)"')
_cur_week = re.compile(r"week=(\d+)[^']*'\s+selected\s*>\s*Week\s+\d+\s*<")


class CookieDead(RuntimeError):
    pass


def player_key(yahoo_id) -> str:
    return f"p.{int(yahoo_id)}"


def _float(t: str):
    m = _num.search(t or "")
    return float(m.group(0)) if m else None


def _dump(body: str, why: str) -> None:
    p = REPO / "cache"
    p.mkdir(exist_ok=True)
    (p / "yahoo_web_fail.html").write_text(body, encoding="utf-8")
    log.error("yahoo_web: %s; page saved to %s", why, p / "yahoo_web_fail.html")


def parse_player_cell(cell) -> dict:
    """The player cell shared by roster and players-list tables."""
    h = cell.html
    m = _pid.search(h)
    if not m:
        return {}
    name = _name.search(h)
    tp = _teampos.search(h)
    team = pos = None
    if tp:
        parts = [x.strip() for x in tp.group(1).split(" - ")]
        if len(parts) == 2:
            team, pos = parts[0].upper(), parts[1]
    st = _status.search(h)
    return {
        "yahoo_id": int(m.group(1)), "player_key": player_key(m.group(1)),
        "name": name.group(1).strip() if name else None,
        "team": team, "pos": pos,
        "game_status": yhtml.text_of(st.group(1)) if st else None,
    }


class YahooWeb:
    def __init__(self):
        cookie = read_secret_text(COOKIE_FILE)
        if not cookie or cookie.startswith("["):
            raise CookieDead("cookie file is empty or still the placeholder")
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Cookie": cookie,
                               "Accept-Language": "en-US,en;q=0.9"})
        self._n = 0

    def get(self, path: str, params: dict = None) -> str:
        if self._n:
            time.sleep(PAUSE_S)
        self._n += 1
        r = self.s.get(f"{BASE}{path}", params=params, timeout=60, allow_redirects=True)
        if "login.yahoo.com" in r.url or r.status_code in (401, 403):
            raise CookieDead(f"redirected to login ({r.url[:60]}); cookie is dead")
        r.raise_for_status()
        return r.text

    # ------------------------------------------------------------ league

    def standings(self) -> dict:
        """{'current_week': N, 'teams': [{team_id, team_name}]}"""
        body = self.get("")
        t = yhtml.find_table(yhtml.tables(body), id="standingstable")
        if not t:
            _dump(body, "no standingstable")
            raise RuntimeError("standings table not found")
        teams = []
        for row in t["rows"]:
            if row[0].tag != "td" or len(row) < 2:
                continue
            m = _team_link.search(row[1].html)
            if m:
                teams.append({"team_id": int(m.group(1)), "team_name": row[1].text})
        m = _cur_week.search(body)
        return {"current_week": int(m.group(1)) if m else None, "teams": teams}

    def current_week(self) -> int:
        body = self.get("/1")
        m = _cur_week.search(body)
        if not m:
            _dump(body, "no selected week option")
            raise RuntimeError("current week not found")
        return int(m.group(1))

    def settings(self) -> dict:
        body = self.get("/settings")
        ts = yhtml.tables(body)
        st = yhtml.find_table(ts, id="settings-table")
        mod = yhtml.find_table(ts, id="settings-stat-mod-table")
        if not st or not mod:
            _dump(body, "settings tables missing")
            raise RuntimeError("settings tables not found")
        kv = {r[0].text.rstrip(":"): r[1].text for r in st["rows"] if len(r) >= 2 and r[0].tag == "td"}
        scoring, group = {}, None
        for r in mod["rows"]:
            if len(r) < 2:
                continue
            if r[0].tag == "th":
                group = r[0].text
                continue
            scoring[f"{group}: {r[0].text.replace(' Yahoo Default', '')}"] = r[1].text
        slots = [x.strip() for x in kv.get("Roster Positions", "").split(",") if x.strip()]
        return {"name": kv.get("League Name"), "settings": kv, "scoring": scoring, "roster_slots": slots}

    # ------------------------------------------------------------ rosters

    def roster(self, team_id: int, week: int) -> list:
        """One team, one week: slot, player, bye, act_pts (Fan Pts), proj_pts, game_status."""
        body = self.get(f"/{team_id}/team", {"week": week})
        ts = yhtml.tables(body)
        out = []
        for tid in ("statTable0", "statTable1", "statTable2"):
            t = yhtml.find_table(ts, id=tid)
            if not t:
                continue
            hi = yhtml.header_index(t["rows"], "Pos", "Bye", "Fan Pts", "Proj Pts")
            if "Fan Pts" not in hi:
                _dump(body, f"{tid}: no Fan Pts header")
                raise RuntimeError(f"roster table {tid} layout changed")
            for row in t["rows"]:
                if row[0].tag != "td" or len(row) <= hi["Proj Pts"]:
                    continue
                p = parse_player_cell(row[1])
                if not p:
                    continue  # empty slot
                bye = _float(row[hi["Bye"]].text)
                p.update({
                    "slot": row[hi["Pos"]].text,
                    "bye_week": int(bye) if bye else None,
                    "act_pts": _float(row[hi["Fan Pts"]].text),
                    "proj_pts": _float(row[hi["Proj Pts"]].text),
                })
                out.append(p)
        if not out:
            _dump(body, f"team {team_id} week {week}: no roster rows")
            raise RuntimeError(f"no roster rows for team {team_id} week {week}")
        return out

    # ------------------------------------------------------------ players list

    def players(self, week: int, kind: str = "proj", groups=("O", "K", "DEF"), depth=DEPTH) -> dict:
        """yahoo_id -> {name, team, pos, bye_week, pts, roster_status} for week N.
        kind='proj' reads S_PW_N (projected), kind='act' reads S_W_N (actual)."""
        stat1 = f"S_PW_{week}" if kind == "proj" else f"S_W_{week}"
        out = {}
        for g in groups:
            got = 0
            for offset in range(0, depth.get(g, 50), PAGE):
                body = self.get("/players", {"status": "ALL", "pos": g, "stat1": stat1,
                                             "sort": "PTS", "sdir": "1", "count": offset})
                t = yhtml.find_table(yhtml.tables(body), cls="Table-interactive")
                if not t:
                    _dump(body, f"players {g} {stat1} offset {offset}: no table")
                    break
                hi = yhtml.header_index(t["rows"], "Roster Status", "Bye", "Fan Pts")
                rows = [r for r in t["rows"] if r and r[0].tag == "td"]
                n = 0
                for row in rows:
                    for c in row[:4]:
                        p = parse_player_cell(c)
                        if p:
                            break
                    if not p or len(row) <= hi.get("Fan Pts", 99):
                        continue
                    bye = _float(row[hi["Bye"]].text) if "Bye" in hi else None
                    p.update({"bye_week": int(bye) if bye else None,
                              "pts": _float(row[hi["Fan Pts"]].text),
                              "roster_status": row[hi["Roster Status"]].text if "Roster Status" in hi else None})
                    out.setdefault(p["yahoo_id"], p)
                    n += 1
                got += n
                if n < PAGE:
                    break
            log.info("yahoo_web players %s week %s %s: %d rows", kind, week, g, got)
        if not out:
            raise RuntimeError(f"yahoo_web: zero players parsed for {stat1}")
        return out


if __name__ == "__main__":
    y = YahooWeb()
    cmd = sys.argv[1] if len(sys.argv) > 1 else "standings"
    if cmd == "standings":
        print(y.standings())
    elif cmd == "settings":
        s = y.settings()
        print(s["name"], s["roster_slots"])
        for k, v in s["scoring"].items():
            print(f"  {k}: {v}")
    elif cmd == "roster":
        for p in y.roster(int(sys.argv[2]), int(sys.argv[3])):
            print(f"  {p['slot']:8} {p['name']:24} {p['team']}-{p['pos']} bye={p['bye_week']} "
                  f"act={p['act_pts']} proj={p['proj_pts']} {p['game_status']}")
    elif cmd == "players":
        d = y.players(int(sys.argv[2]), sys.argv[3] if len(sys.argv) > 3 else "proj")
        top = sorted(d.values(), key=lambda p: -(p["pts"] or 0))[:10]
        print(len(d), "players; top:")
        for p in top:
            print(f"  {p['name']:24} {p['team']}-{p['pos']} {p['pts']} [{p['roster_status']}]")
