"""Projected points from the Yahoo fantasy website. The API has no projections resource
(checked 2026-09-14), so this scrapes the league's players list with Will's login cookie.

Secret: `.secrets/yahoo_cookie.txt`, the raw `Cookie:` header value copied from a logged-in
browser request to football.fantasysports.yahoo.com. A dead cookie raises CookieDead; the
AM job records rosters anyway and marks the run error='cookie'.

Page: /f1/{league}/players?status=ALL&pos={POS}&stat1=S_PW_{week}&sort=PTS&sdir=1&count={offset}
25 rows per page. Each row has a link to sports.yahoo.com/nfl/players/{yahoo_id} and a
points cell under the projected-week header. The parser reads the header to find that
column, so a shuffled layout is survivable; a redesigned page is not, and dumps HTML to
cache/yahoo_web_fail.html for a human to look at.
"""
from __future__ import annotations

import html
import re
import sys
from pathlib import Path

import requests

from .common import LEAGUE_ID, REPO, log, read_secret_text

COOKIE_FILE = "yahoo_cookie.txt"
BASE = f"https://football.fantasysports.yahoo.com/f1/{LEAGUE_ID}/players"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0 Safari/537.36")
POSITIONS = ["QB", "RB", "WR", "TE", "K", "DEF"]
PAGE = 25
# how deep to go per position: rostered players in a 10-team league plus a free-agent tail
DEPTH = {"QB": 50, "RB": 100, "WR": 125, "TE": 50, "K": 32, "DEF": 32}

_row_re = re.compile(r"<tr\b[^>]*>(.*?)</tr>", re.S | re.I)
_cell_re = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.S | re.I)
_pid_re = re.compile(r"/nfl/players/(\d+)")
_tag_re = re.compile(r"<[^>]+>")
_ws_re = re.compile(r"\s+")


class CookieDead(RuntimeError):
    pass


def _text(fragment: str) -> str:
    return _ws_re.sub(" ", html.unescape(_tag_re.sub(" ", fragment))).strip()


def _dump(body: str, why: str) -> None:
    p = REPO / "cache"
    p.mkdir(exist_ok=True)
    (p / "yahoo_web_fail.html").write_text(body, encoding="utf-8")
    log.error("yahoo_web: %s; page saved to %s", why, p / "yahoo_web_fail.html")


def fetch_page(session: requests.Session, pos: str, week: int, offset: int) -> str:
    params = {"status": "ALL", "pos": pos, "stat1": f"S_PW_{week}", "sort": "PTS",
              "sdir": "1", "count": str(offset)}
    r = session.get(BASE, params=params, timeout=60, allow_redirects=True)
    if "login.yahoo.com" in r.url or r.status_code in (401, 403):
        raise CookieDead(f"redirected to login ({r.url[:60]}); cookie is dead")
    r.raise_for_status()
    return r.text


def parse_page(body: str) -> list:
    """[(yahoo_id, proj_pts, name_text)] for every player row on the page."""
    rows = _row_re.findall(body)
    pts_col = None
    for row in rows:
        cells = _cell_re.findall(row)
        texts = [_text(c) for c in cells]
        if not _pid_re.search(row):
            # header row: locate the projected points column
            for i, t in enumerate(texts):
                if re.search(r"\b(proj|fan pts|points|pts)\b", t, re.I):
                    pts_col = i
                    break
            continue
    out = []
    for row in rows:
        m = _pid_re.search(row)
        if not m:
            continue
        cells = _cell_re.findall(row)
        texts = [_text(c) for c in cells]
        pts = None
        if pts_col is not None and pts_col < len(texts):
            pts = _num(texts[pts_col])
        if pts is None:
            # fallback: first cell after the player cell that parses as a decimal number
            for t in texts:
                v = _num(t)
                if v is not None and "." in t:
                    pts = v
                    break
        name = next((t for t in texts if _pid_re.search(str(t)) is None and len(t) > 3), "")
        out.append((int(m.group(1)), pts, name[:60]))
    return out


def _num(t: str):
    m = re.fullmatch(r"-?\d+(?:\.\d+)?", t.replace(",", ""))
    return float(m.group(0)) if m else None


def projections(week: int, positions=POSITIONS, depth=DEPTH) -> dict:
    """yahoo_id -> proj_pts for `week`, across positions. Raises CookieDead if the
    cookie no longer logs in."""
    cookie = read_secret_text(COOKIE_FILE)
    if not cookie or cookie.startswith("["):
        raise CookieDead("cookie file is empty or still the placeholder")
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Cookie": cookie, "Accept-Language": "en-US,en;q=0.9"})
    out = {}
    for pos in positions:
        got = 0
        for offset in range(0, depth.get(pos, 50), PAGE):
            body = fetch_page(s, pos, week, offset)
            rows = parse_page(body)
            if not rows:
                if offset == 0:
                    _dump(body, f"no player rows parsed for pos={pos} week={week}")
                break
            for pid, pts, _ in rows:
                if pts is not None and pid not in out:
                    out[pid] = pts
            got += len(rows)
            if len(rows) < PAGE:
                break
        log.info("yahoo_web week %s %s: %d rows", week, pos, got)
    if not out:
        raise RuntimeError("yahoo_web: zero projections parsed; page layout changed?")
    return out


if __name__ == "__main__":
    week = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    if len(sys.argv) > 2 and Path(sys.argv[2]).exists():  # parse a saved page offline
        for r in parse_page(Path(sys.argv[2]).read_text(encoding="utf-8"))[:10]:
            print(r)
    else:
        p = projections(week)
        top = sorted(p.items(), key=lambda kv: -kv[1])[:10]
        print(f"{len(p)} projections for week {week}; top: {top}")
