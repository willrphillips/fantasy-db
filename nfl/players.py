"""The `players` table: Yahoo player_key is the id, nflverse gsis_id is mapped onto it.

Mapping order: nflverse rosters carry a `yahoo_id` for most players (1147 of 2963 rows on
2026-09-14), so that join is first. What is left is matched by normalised name + team,
then name alone if unique. Anything still unmapped is reported, never guessed.
"""
from __future__ import annotations

import re
import unicodedata

from .common import now_utc


def norm(name: str) -> str:
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"\s+(jr|sr|ii|iii|iv|v)\.?$", "", s)
    s = re.sub(r"[^\w\s]", "", s)
    return re.sub(r"\s+", " ", s).strip()


# Yahoo team abbreviations that differ from nflverse
TEAM_ALIAS = {"JAC": "JAX", "WAS": "WAS", "LAR": "LA", "LV": "LV", "HOU": "HOU"}


def upsert_yahoo(conn, players: list) -> int:
    """players: dicts with player_key, yahoo_id, name, team, pos, bye_week (from yahoo_api)."""
    conn.executemany(
        "INSERT INTO players (player_key, yahoo_id, name, team, pos, bye_week, updated_at) "
        "VALUES (:player_key, :yahoo_id, :name, :team, :pos, :bye_week, :updated_at) "
        "ON CONFLICT(player_key) DO UPDATE SET yahoo_id=excluded.yahoo_id, name=excluded.name, "
        "team=excluded.team, pos=excluded.pos, bye_week=COALESCE(excluded.bye_week, players.bye_week), "
        "updated_at=excluded.updated_at",
        [dict(p, updated_at=now_utc()) for p in players])
    conn.commit()
    return len(players)


def map_gsis(conn, nfl_rosters: list) -> dict:
    """Fill players.gsis_id from nflverse rosters. Returns counts and the unresolved list."""
    by_yid = {r["yahoo_id"]: r["gsis_id"] for r in nfl_rosters if r["yahoo_id"] and r["gsis_id"]}
    by_name_team, by_name = {}, {}
    for r in nfl_rosters:
        if not r["gsis_id"]:
            continue
        n = norm(r["name"])
        by_name_team[(n, r["team"])] = r["gsis_id"]
        by_name.setdefault(n, set()).add(r["gsis_id"])

    rows = conn.execute("SELECT player_key, yahoo_id, name, team, pos FROM players").fetchall()
    n_yid = n_name = 0
    unresolved = []
    updates = []
    for p in rows:
        if p["pos"] == "DEF":
            continue  # team defenses have no gsis_id
        g = by_yid.get(p["yahoo_id"])
        if g:
            n_yid += 1
        else:
            n = norm(p["name"])
            team = TEAM_ALIAS.get((p["team"] or "").upper(), (p["team"] or "").upper())
            g = by_name_team.get((n, team))
            if not g and len(by_name.get(n, ())) == 1:
                g = next(iter(by_name[n]))
            if g:
                n_name += 1
            else:
                unresolved.append(f"{p['name']} ({p['team']} {p['pos']}, {p['player_key']})")
        if g:
            updates.append((g, p["player_key"]))
    conn.executemany("UPDATE players SET gsis_id=? WHERE player_key=?", updates)
    conn.commit()
    return {"by_yahoo_id": n_yid, "by_name": n_name, "unresolved": unresolved}
