"""nflverse release CSVs: schedules, weekly player stats, rosters. No key needed.

URLs checked 2026-09-14:
  schedules  .../releases/download/schedules/games.csv            (all seasons, one file)
  stats      .../releases/download/stats_player/stats_player_week_{season}.csv
  rosters    .../releases/download/rosters/roster_{season}.csv    (has yahoo_id + gsis_id)

Everything is parsed with the stdlib csv module; no pandas on the Mac.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

import requests

from .common import ET, log, now_utc

BASE = "https://github.com/nflverse/nflverse-data/releases/download"
UA = {"User-Agent": "fantasy-db nfl pipeline (github.com/willrphillips/fantasy-db)"}


def _csv(url: str) -> list:
    r = requests.get(url, headers=UA, timeout=120)
    r.raise_for_status()
    text = r.content.decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def _int(v):
    if v in (None, "", "NA"):
        return None
    try:
        return int(float(v))
    except ValueError:
        return None


def kickoff_utc(gameday: str, gametime: str) -> str:
    """nflverse gameday 'YYYY-MM-DD' + gametime 'HH:MM' (ET) -> ISO UTC."""
    if not gametime:
        gametime = "13:00"
    dt = datetime.strptime(f"{gameday} {gametime}", "%Y-%m-%d %H:%M").replace(tzinfo=ET)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def schedules(season: int) -> list:
    """Regular season games for `season` as rows shaped for the `games` table."""
    rows = _csv(f"{BASE}/schedules/games.csv")
    out = []
    for r in rows:
        if _int(r["season"]) != season or r.get("game_type") != "REG":
            continue
        hs, as_ = _int(r["home_score"]), _int(r["away_score"])
        out.append({
            "game_id": r["game_id"], "season": season, "week": _int(r["week"]),
            "kickoff_utc": kickoff_utc(r["gameday"], r["gametime"]),
            "home": r["home_team"], "away": r["away_team"],
            "home_score": hs, "away_score": as_,
            "status": "final" if hs is not None and as_ is not None else "scheduled",
        })
    log.info("nflverse schedules %s: %d REG games", season, len(out))
    return out


def upsert_games(conn, games: list) -> int:
    conn.executemany(
        "INSERT INTO games (game_id, season, week, kickoff_utc, home, away, home_score, away_score, status) "
        "VALUES (:game_id, :season, :week, :kickoff_utc, :home, :away, :home_score, :away_score, :status) "
        "ON CONFLICT(game_id) DO UPDATE SET kickoff_utc=excluded.kickoff_utc, home=excluded.home, "
        "away=excluded.away, home_score=excluded.home_score, away_score=excluded.away_score, "
        "status=excluded.status", games)
    conn.execute("DELETE FROM weeks WHERE season=?", (games[0]["season"],) if games else (0,))
    conn.execute(
        "INSERT INTO weeks (season, week, first_kickoff_utc, last_kickoff_utc) "
        "SELECT season, week, MIN(kickoff_utc), MAX(kickoff_utc) FROM games GROUP BY season, week")
    conn.commit()
    return len(games)


STAT_COLS = {
    "pass_att": "attempts", "pass_cmp": "completions", "pass_yds": "passing_yards",
    "pass_td": "passing_tds", "pass_int": "passing_interceptions",
    "rush_att": "carries", "rush_yds": "rushing_yards", "rush_td": "rushing_tds",
    "tgt": "targets", "rec": "receptions", "rec_yds": "receiving_yards", "rec_td": "receiving_tds",
    "fg_made": "fg_made", "fg_att": "fg_att", "xp_made": "pat_made",
}


def player_stats(season: int, week: int = None) -> list:
    """Weekly rows shaped for the `stats` table. snaps is not in this file; left NULL."""
    rows = _csv(f"{BASE}/stats_player/stats_player_week_{season}.csv")
    out = []
    for r in rows:
        if r.get("season_type") != "REG":
            continue
        w = _int(r["week"])
        if week is not None and w != week:
            continue
        rec = {"season": season, "week": w, "gsis_id": r["player_id"], "pulled_at": now_utc(), "snaps": None}
        for k, src in STAT_COLS.items():
            rec[k] = _int(r.get(src)) or 0
        rec["fum_lost"] = sum((_int(r.get(c)) or 0) for c in
                              ("sack_fumbles_lost", "rushing_fumbles_lost", "receiving_fumbles_lost"))
        rec["two_pt"] = sum((_int(r.get(c)) or 0) for c in
                            ("passing_2pt_conversions", "rushing_2pt_conversions", "receiving_2pt_conversions"))
        out.append(rec)
    log.info("nflverse player_stats %s week=%s: %d rows", season, week, len(out))
    return out


def upsert_stats(conn, rows: list) -> int:
    cols = ["season", "week", "gsis_id"] + list(STAT_COLS) + ["fum_lost", "two_pt", "snaps", "pulled_at"]
    sql = (f"INSERT OR REPLACE INTO stats ({', '.join(cols)}) "
           f"VALUES ({', '.join(':' + c for c in cols)})")
    conn.executemany(sql, rows)
    conn.commit()
    return len(rows)


def rosters(season: int) -> list:
    """Latest nflverse roster rows: gsis_id, yahoo_id, name, team, position, status."""
    rows = _csv(f"{BASE}/rosters/roster_{season}.csv")
    out = []
    for r in rows:
        out.append({
            "gsis_id": r.get("gsis_id") or None,
            "yahoo_id": _int(r.get("yahoo_id")),
            "name": r.get("full_name"), "team": r.get("team"),
            "pos": r.get("position"), "status": r.get("status"),
        })
    log.info("nflverse rosters %s: %d rows", season, len(out))
    return out


if __name__ == "__main__":
    import sys
    season = int(sys.argv[1]) if len(sys.argv) > 1 else datetime.now(ET).year
    g = schedules(season)
    print(f"games: {len(g)}, weeks {min(x['week'] for x in g)}-{max(x['week'] for x in g)}, "
          f"final: {sum(1 for x in g if x['status'] == 'final')}")
    s = player_stats(season)
    print(f"stats rows: {len(s)}, weeks: {sorted({x['week'] for x in s})}")
    ro = rosters(season)
    print(f"roster rows: {len(ro)}, with yahoo_id: {sum(1 for x in ro if x['yahoo_id'])}")
