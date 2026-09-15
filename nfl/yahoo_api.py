"""Yahoo Fantasy Sports API: OAuth2 refresh flow plus league, teams, rosters, scoring,
actual points. Projected points are NOT here; the API has no such resource. See yahoo_web.

Secrets: `.secrets/yahoo.json`
    {"client_id": "...", "client_secret": "...", "refresh_token": "...",
     "access_token": "...", "expires_at": 0}
Only client_id and client_secret are typed by hand; `auth` fills the rest.

One-time authorize, on the Mac:
    python -m nfl.yahoo_api auth          # prints the URL to open in a browser
    ...log in, Agree; the browser lands on the redirect URI with ?code=... in the address
       bar and cannot connect. Paste that whole URL into .secrets/yahoo_code.txt...
    python -m nfl.yahoo_api auth          # reads the code, stores the refresh token
    python -m nfl.yahoo_api whoami        # league 206739 / team 1 check

Responses are requested as XML and parsed with ElementTree. Yahoo's JSON is a mess of
numeric-keyed arrays; the XML is the same data with a sane shape.
"""
from __future__ import annotations

import sys
import time
import xml.etree.ElementTree as ET_
from urllib.parse import parse_qs, quote, urlparse

import requests

from .common import LEAGUE_ID, SECRETS, WILL_TEAM_ID, log, read_secret_json, write_secret_json

AUTH_URL = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN_URL = "https://api.login.yahoo.com/oauth2/get_token"
API = "https://fantasysports.yahooapis.com/fantasy/v2"
SECRET_FILE = "yahoo.json"
# Yahoo dropped `oob` for newer apps; the app registers a real redirect URI and the code
# comes back in the browser address bar (localhost refuses the connection, which is fine).
REDIRECT_URI = "https://localhost:8080"
CODE_FILE = SECRETS / "yahoo_code.txt"
CHUNK = 25  # max player_keys per request


# ---------------------------------------------------------------- OAuth

class Yahoo:
    def __init__(self):
        self.sec = read_secret_json(SECRET_FILE)
        for k in ("client_id", "client_secret"):
            if not self.sec.get(k) or self.sec[k].startswith("["):
                raise RuntimeError(f"{SECRETS / SECRET_FILE}: {k} not filled in")
        self.s = requests.Session()

    def _token_request(self, **form) -> dict:
        form.update(client_id=self.sec["client_id"], client_secret=self.sec["client_secret"],
                    redirect_uri=self.sec.get("redirect_uri", REDIRECT_URI))
        r = self.s.post(TOKEN_URL, data=form, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"yahoo token {form.get('grant_type')}: {r.status_code} {r.text[:300]}")
        tok = r.json()
        self.sec["access_token"] = tok["access_token"]
        self.sec["refresh_token"] = tok.get("refresh_token", self.sec.get("refresh_token"))
        self.sec["expires_at"] = int(time.time()) + int(tok.get("expires_in", 3600)) - 60
        write_secret_json(SECRET_FILE, self.sec)
        return tok

    def authorize_url(self) -> str:
        ru = quote(self.sec.get("redirect_uri", REDIRECT_URI), safe="")
        return (f"{AUTH_URL}?client_id={self.sec['client_id']}&redirect_uri={ru}"
                f"&response_type=code&language=en-us")

    def exchange_code(self, code: str) -> None:
        self._token_request(grant_type="authorization_code", code=code.strip())
        log.info("yahoo: refresh token stored in %s", SECRETS / SECRET_FILE)

    def token(self) -> str:
        if not self.sec.get("refresh_token"):
            raise RuntimeError("no refresh_token; run `python -m nfl.yahoo_api auth` first")
        if not self.sec.get("access_token") or time.time() >= int(self.sec.get("expires_at", 0)):
            self._token_request(grant_type="refresh_token", refresh_token=self.sec["refresh_token"])
        return self.sec["access_token"]

    # ------------------------------------------------------------ transport

    def get(self, path: str) -> ET_.Element:
        """GET an API path (no leading slash needed), return the root element, namespaces stripped."""
        url = f"{API}/{path.lstrip('/')}"
        for attempt in (1, 2):
            r = self.s.get(url, headers={"Authorization": f"Bearer {self.token()}"}, timeout=60)
            if r.status_code == 401 and attempt == 1:
                self.sec["expires_at"] = 0  # force refresh once
                continue
            break
        if r.status_code != 200:
            raise RuntimeError(f"yahoo GET {path}: {r.status_code} {r.text[:300]}")
        root = ET_.fromstring(r.content)
        for el in root.iter():
            if "}" in el.tag:
                el.tag = el.tag.split("}", 1)[1]
        return root

    # ------------------------------------------------------------ resources

    def game(self) -> dict:
        g = self.get("game/nfl").find("game")
        return {"game_key": g.findtext("game_key"), "season": int(g.findtext("season")),
                "is_game_over": g.findtext("is_game_over")}

    def league_key(self) -> str:
        if not self.sec.get("league_key"):
            self.sec["league_key"] = f"{self.game()['game_key']}.l.{LEAGUE_ID}"
            write_secret_json(SECRET_FILE, self.sec)
        return self.sec["league_key"]

    def league(self) -> dict:
        lk = self.league_key()
        el = self.get(f"league/{lk}").find("league")
        return {
            "league_key": lk, "season": int(el.findtext("season")), "name": el.findtext("name"),
            "current_week": int(el.findtext("current_week") or 0),
            "start_week": int(el.findtext("start_week") or 1),
            "end_week": int(el.findtext("end_week") or 17),
            "num_teams": int(el.findtext("num_teams") or 0),
        }

    def settings(self) -> dict:
        """Scoring modifiers keyed by stat name, and roster slots with counts."""
        el = self.get(f"league/{self.league_key()}/settings").find("league/settings")
        names = {s.findtext("stat_id"): s.findtext("name")
                 for s in el.findall("stat_categories/stats/stat")}
        scoring = {names.get(s.findtext("stat_id"), s.findtext("stat_id")): float(s.findtext("value"))
                   for s in el.findall("stat_modifiers/stats/stat")}
        slots = {rp.findtext("position"): int(rp.findtext("count"))
                 for rp in el.findall("roster_positions/roster_position")}
        return {"scoring": scoring, "roster_slots": slots}

    def teams(self) -> list:
        lk = self.league_key()
        out = []
        for t in self.get(f"league/{lk}/teams").findall("league/teams/team"):
            tid = int(t.findtext("team_id"))
            out.append({
                "team_key": t.findtext("team_key"), "league_key": lk, "team_id": tid,
                "team_name": t.findtext("name"),
                "manager": t.findtext("managers/manager/nickname"),
                "is_will": 1 if tid == WILL_TEAM_ID else 0,
            })
        return out

    @staticmethod
    def _player(p: ET_.Element) -> dict:
        bye = p.findtext("bye_weeks/week")
        return {
            "player_key": p.findtext("player_key"),
            "yahoo_id": int(p.findtext("player_id")),
            "name": p.findtext("name/full"),
            "team": p.findtext("editorial_team_abbr"),
            "pos": p.findtext("display_position"),
            "bye_week": int(bye) if bye and bye.isdigit() else None,
        }

    def roster(self, team_key: str, week: int) -> list:
        """Players on one team for `week` with their selected slot."""
        root = self.get(f"team/{team_key}/roster;week={week}")
        out = []
        for p in root.findall("team/roster/players/player"):
            d = self._player(p)
            d["slot"] = p.findtext("selected_position/position")
            d["team_key"] = team_key
            out.append(d)
        return out

    def free_agents(self, position: str = None, count: int = 50) -> list:
        lk = self.league_key()
        out, start = [], 0
        while len(out) < count:
            path = f"league/{lk}/players;status=A;sort=AR;start={start};count={min(25, count - len(out))}"
            if position:
                path += f";position={position}"
            ps = self.get(path).findall("league/players/player")
            if not ps:
                break
            out.extend(self._player(p) for p in ps)
            start += len(ps)
        return out

    def players(self, player_keys: list) -> list:
        """Bio rows for arbitrary player_keys (name, team, pos, bye), 25 per request."""
        lk = self.league_key()
        out = []
        keys = list(player_keys)
        for i in range(0, len(keys), CHUNK):
            chunk = ",".join(keys[i:i + CHUNK])
            root = self.get(f"league/{lk}/players;player_keys={chunk}")
            out.extend(self._player(p) for p in root.findall("league/players/player"))
        return out

    def week_points(self, player_keys: list, week: int) -> dict:
        """player_key -> actual fantasy points under this league's scoring for `week`."""
        lk = self.league_key()
        out = {}
        keys = list(player_keys)
        for i in range(0, len(keys), CHUNK):
            chunk = ",".join(keys[i:i + CHUNK])
            root = self.get(f"league/{lk}/players;player_keys={chunk}/stats;type=week;week={week}")
            for p in root.findall("league/players/player"):
                total = p.findtext("player_points/total")
                out[p.findtext("player_key")] = float(total) if total not in (None, "") else None
        return out


# ---------------------------------------------------------------- CLI

def cmd_auth() -> int:
    y = Yahoo()
    if CODE_FILE.exists():
        code = CODE_FILE.read_text(encoding="utf-8").strip()
        if code.startswith("http"):  # the whole redirected URL was pasted; pull the code out
            code = parse_qs(urlparse(code).query).get("code", [code])[0]
        y.exchange_code(code)
        CODE_FILE.unlink()
        print("refresh token stored; run `python -m nfl.yahoo_api whoami`")
        return 0
    if y.sec.get("refresh_token"):
        print("already authorized; delete refresh_token from .secrets/yahoo.json to redo")
        return 0
    print("1. Open this URL in a browser, log in as Will, click Agree:\n")
    print("   " + y.authorize_url() + "\n")
    print(f"2. The browser lands on {REDIRECT_URI}/?code=... and fails to connect.")
    print(f"   Copy that whole address bar URL into {CODE_FILE}")
    print("3. Run `python -m nfl.yahoo_api auth` again.")
    return 0


def cmd_whoami() -> int:
    y = Yahoo()
    lg = y.league()
    print(f"game_key={lg['league_key'].split('.')[0]} season={lg['season']}")
    print(f"league {LEAGUE_ID}: {lg['name']!r} teams={lg['num_teams']} current_week={lg['current_week']}")
    me = [t for t in y.teams() if t["is_will"]]
    if not me:
        print(f"team {WILL_TEAM_ID} NOT FOUND", file=sys.stderr)
        return 1
    print(f"team {WILL_TEAM_ID}: {me[0]['team_name']!r} manager={me[0]['manager']!r} key={me[0]['team_key']}")
    return 0


def main(argv):
    cmds = {"auth": cmd_auth, "whoami": cmd_whoami}
    if not argv or argv[0] not in cmds:
        print(f"usage: python -m nfl.yahoo_api {{{'|'.join(cmds)}}}", file=sys.stderr)
        return 2
    return cmds[argv[0]]()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
