# NFL_PLAN — fantasy-db, NFL season (drafted 2026-09-14 by the C:\Code manager seat)

> **Amended 2026-09-15.** Shipped; see `SCOPE_OF_WORK.md` 2026-09-15. One fixed fact
> below is wrong: the Yahoo Fantasy API is gated behind an approval queue, so the whole
> Yahoo read path is the website with the login cookie (`nfl/yahoo_web.py`), and player
> keys are `p.<yahoo_id>`, team keys `t.N`, league key `l.206739`. `yahoo_api.py` is
> retired on disk pending Will's application. The rest of this file stands as written.

Read with `SCOPE_OF_WORK.md` entry 2026-09-14. This is the handover spec. Sections fill in
as each step is decided with Will. Nothing here runs on atlas; MLB is retired there.

## Fixed facts

| Item | Value |
|---|---|
| Runs on | old-will-macbook, Tailscale `100.126.114.42`, macOS 12.7.6, ssh `willrphillips` |
| Trigger | launchd only. Zero AI tokens in the pipeline. |
| Yahoo league | `https://football.fantasysports.yahoo.com/f1/206739/1` -> league id `206739`, Will's team id `1` |
| Yahoo API | Will has a dev app (client id + secret). Used for league, rosters, scoring, actual points. |
| Yahoo web | Login cookie. Used ONLY for expected (projected) points; the API does not expose them. |
| NFL raw stats | nflverse release CSVs (schedules, player_stats, rosters). No key. |
| DB | SQLite `nfl.db`, one file, on the Mac. |

## Schema (step 5, decided 2026-09-14)

Keys: Yahoo `player_key` (e.g. `461.p.12345`) is the primary player id. nflverse `gsis_id` is
mapped once per player in `players`; `name_matcher.py` from the MLB side is reusable for the
first pass. `(season, week)` is the grain everywhere; no game-level fantasy points, Yahoo
only scores weeks.

```sql
CREATE TABLE weeks (
  season INTEGER, week INTEGER,
  first_kickoff_utc TEXT, last_kickoff_utc TEXT,   -- from nflverse schedules
  PRIMARY KEY (season, week)
);

CREATE TABLE games (                               -- nflverse schedules, refreshed weekly
  game_id TEXT PRIMARY KEY, season INTEGER, week INTEGER,
  kickoff_utc TEXT, home TEXT, away TEXT,
  home_score INTEGER, away_score INTEGER, status TEXT   -- scheduled | final
);

CREATE TABLE players (
  player_key TEXT PRIMARY KEY,                     -- Yahoo
  yahoo_id INTEGER, gsis_id TEXT,                  -- nflverse join
  name TEXT, team TEXT, pos TEXT, bye_week INTEGER,
  updated_at TEXT
);

CREATE TABLE league (
  league_key TEXT PRIMARY KEY, season INTEGER, name TEXT,
  scoring_json TEXT, roster_slots_json TEXT, pulled_at TEXT
);

CREATE TABLE league_teams (
  team_key TEXT PRIMARY KEY, league_key TEXT, team_id INTEGER,
  team_name TEXT, manager TEXT, is_will INTEGER    -- team_id 1 = Will
);

CREATE TABLE rosters (                             -- snapshot taken by the AM job
  season INTEGER, week INTEGER, team_key TEXT, player_key TEXT,
  slot TEXT,                                       -- QB RB WR TE FLEX K DEF BN IR
  pulled_at TEXT,
  PRIMARY KEY (season, week, team_key, player_key)
);

CREATE TABLE projections (                         -- AM job, Yahoo web
  season INTEGER, week INTEGER, player_key TEXT,
  proj_pts REAL, opp TEXT, game_id TEXT,
  pulled_at TEXT, source TEXT DEFAULT 'yahoo_web',
  PRIMARY KEY (season, week, player_key)           -- last pull of the day wins
);

CREATE TABLE actuals (                             -- PM job, Yahoo API, league scoring
  season INTEGER, week INTEGER, player_key TEXT,
  act_pts REAL, pulled_at TEXT, is_final INTEGER,  -- 0 while games in week still open
  PRIMARY KEY (season, week, player_key)
);

CREATE TABLE stats (                               -- PM job, nflverse player_stats
  season INTEGER, week INTEGER, gsis_id TEXT,
  pass_att INTEGER, pass_cmp INTEGER, pass_yds INTEGER, pass_td INTEGER, pass_int INTEGER,
  rush_att INTEGER, rush_yds INTEGER, rush_td INTEGER,
  tgt INTEGER, rec INTEGER, rec_yds INTEGER, rec_td INTEGER,
  fum_lost INTEGER, two_pt INTEGER,
  fg_made INTEGER, fg_att INTEGER, xp_made INTEGER,
  snaps INTEGER, pulled_at TEXT,
  PRIMARY KEY (season, week, gsis_id)
);

CREATE TABLE runs (                                -- every launchd fire, success or not
  id INTEGER PRIMARY KEY, job TEXT, started_at TEXT, ended_at TEXT,
  ok INTEGER, rows INTEGER, error TEXT
);

CREATE VIEW player_week AS                         -- the table Edwin actually reads
SELECT p.season, p.week, pl.player_key, pl.name, pl.team, pl.pos,
       p.proj_pts, a.act_pts, a.act_pts - p.proj_pts AS delta,
       a.is_final, r.team_key AS rostered_by, r.slot
FROM projections p
JOIN players pl USING (player_key)
LEFT JOIN actuals a USING (season, week, player_key)
LEFT JOIN rosters r USING (season, week, player_key);
```

Retention: everything kept for the season. `projections` is one row per player per week
(the AM pull overwrites); if Will later wants intra-week projection drift, add `pulled_at`
to the key. Not now.

## Jobs (step 6, decided 2026-09-14: option B, AM 07:00 / PM 02:00 ET)

Mac facts checked 2026-09-14: no Homebrew, no node, system Python 3.9.6 with pip 21 and
SQLite 3.37. That is enough. Use a venv on system Python; do not install Homebrew for this.
Pattern to copy: `~/Library/LaunchAgents/com.willr.capcom-qbosync.plist` (sh wrapper, log to
`~/Library/Logs/`). Checkout lives at `~/fantasy-db` (sibling of `~/capcom`).

| LaunchAgent | Fires (local, Mac is ET) | Script | Does |
|---|---|---|---|
| `com.willr.nfl-am` | daily 07:00 | `nfl/am.py` | If a game kicks off today (per `games`): pull Yahoo rosters for all league teams and projections for every rostered + top free-agent player. Else exit 0, log "no games". |
| `com.willr.nfl-pm` | daily 02:00 | `nfl/pm.py` | If a game finished yesterday: pull Yahoo actual points for the week, pull nflverse `player_stats` for the week, refresh `games` scores. Mark `actuals.is_final=1` once every game in the week is final. Else exit 0. |
| `com.willr.nfl-weekly` | Tuesday 05:00 | `nfl/weekly.py` | nflverse schedules + rosters, Yahoo league meta/scoring, `players` map refresh, unresolved-name report into `runs.error`. |

Rules
- Every script opens with a `runs` row and closes it; a crash still writes `ok=0` + traceback.
- Retries: 3 attempts, 5-minute gap, inside the script. launchd itself never retries.
- Yahoo OAuth refresh token lives in `~/fantasy-db/.secrets/yahoo.json` (chmod 600, gitignored).
  Client id/secret go in the same file. The login cookie for projections goes in
  `~/fantasy-db/.secrets/yahoo_cookie.txt`. A dead cookie fails only the projections step; the
  run still records rosters and returns ok=1 with `error='cookie'` so the AM row is visible.
- No Discord, no Edwin, no AI in any of the three. A failure shows up as `runs.ok=0`; Edwin's
  read path (step 7) surfaces it when asked.
- Times are wall-clock ET via `StartCalendarInterval`; the Mac already runs in ET.

## Edwin read path (step 7, decided 2026-09-14: option A, live HTTP over Tailscale)

Mirror of GOB Books (`edwin/docs/SHARED_BOOKS_API.md`, atlas `:8093`), but on the Mac and
personal, not shared. Port `8094` is free on the Mac (checked 2026-09-14: only Dropbox,
CAPCOM `:4180`, rapportd listen). Bind to the Tailscale address only, never `0.0.0.0`.

| Piece | Value |
|---|---|
| Server | `nfl/serve.py`, stdlib `http.server` + sqlite3, read-only connection (`?mode=ro`) |
| Listens | `100.126.114.42:8094` (Tailscale IP; also `old-will-macbook.tail696e25.ts.net`) |
| LaunchAgent | `com.willr.nfl-serve`, `KeepAlive true`, `RunAtLoad true` |
| Auth | `Authorization: Bearer <key>`; key file `~/fantasy-db/.secrets/serve-key` (600) on the Mac, `~/.nfl-key` (600) on atlas for Edwin. `/health` needs no key. |
| Log | every call to `~/Library/Logs/nfl-serve.log`; CAPCOM can tail it later, not in scope now |

Endpoints (all GET, JSON)

| Ask | Call |
|---|---|
| liveness | `/health` |
| freshness: last AM/PM run, ok flag, row counts | `/status` |
| one player, this week or `?week=N` | `/player?name=Bijan%20Robinson` (fuzzy on `players.name`, returns all matches if ambiguous) |
| Will's roster with proj + actual | `/roster?team=1&week=N` |
| any league team's roster | `/roster?team=K` |
| proj vs actual for the week, whole league or `?pos=WR` | `/week?week=N` |
| biggest over/under-performers season to date | `/deltas?pos=RB&n=20` |
| free agents by proj this week | `/fa?pos=TE&n=15` |
| raw SQL, SELECT only, 5 s timeout, 500-row cap | `/sql?q=...` (for Edwin's ad-hoc questions; anything but SELECT is 400) |

Edwin side (edwin repo, separate handover after this one lands)
- One tool, `nfl`, wrapping the calls above. Same shape as `~/bin/gob`.
- Edwin answers fantasy questions from this API and nothing else: no live Yahoo/ESPN pulls,
  no web search for stats. If `/health` fails he says the Mac is unreachable and stops.
- The MLB `fantasy_lib`/snapshot reading in `bot.py` is dead (symlink removed on atlas
  2026-09-14); the edwin worker removes those three loops when it adds the `nfl` tool.

## Handover checklist (step 8) — for the fantasy-db worker session

Order matters. Each line has its "done" and the command the manager seat runs to verify it
from outside. Do not skip to the next until the check passes. Do not use AI calls anywhere in
the pipeline. Do not touch atlas.

| # | Task | Done when | Manager verifies with |
|---|---|---|---|
| 1 | Fix stale docs: `CLAUDE.md` / `README.md` say folder is `mlbstats`, repo is `fantasy-bot`. Now `fantasy-db` both. MLB section stays, marked RETIRED 2026-09-14. | grep finds no live `mlbstats` / `fantasy-bot` references | `grep -rn "mlbstats\|fantasy-bot" *.md` |
| 2 | Create `nfl/` package: `db.py` (schema above, `init` idempotent), `yahoo_api.py` (OAuth2 refresh flow, league/rosters/scoring/actuals), `yahoo_web.py` (cookie, projections), `nflverse.py` (schedules, player_stats, rosters CSV), `am.py`, `pm.py`, `weekly.py`, `serve.py`. `requirements-nfl.txt`. | `python -m nfl.db init` creates `nfl.db` with all tables + view | `sqlite3 nfl.db .tables` |
| 3 | `.secrets/` gitignored, with `yahoo.json.example` and `yahoo_cookie.txt.example`. Will fills real values ON THE MAC, never in chat. | `.gitignore` has `.secrets/`, `nfl.db` | `git check-ignore .secrets/yahoo.json` |
| 4 | Clone to Mac `~/fantasy-db`, venv on system Python 3.9, install requirements. | `~/fantasy-db/venv/bin/python -c "import nfl"` exits 0 | ssh + that command |
| 5 | Will pastes Yahoo client id/secret into `~/fantasy-db/.secrets/yahoo.json` on the Mac; worker runs the one-time OAuth authorize (`python -m nfl.yahoo_api auth`) and stores the refresh token. | `python -m nfl.yahoo_api whoami` prints league 206739 and team 1 | ssh + that command |
| 6 | `weekly.py` run once by hand. | `games`, `players`, `league`, `league_teams` populated for 2026 | `sqlite3 nfl.db "select count(*) from games where season=2026"` |
| 7 | `am.py` run once by hand on a game day (or `--force`). | `rosters` + `projections` rows for the current week; `runs.ok=1` | `sqlite3 nfl.db "select job,ok,rows,error from runs order by id desc limit 3"` |
| 8 | `pm.py` run once by hand on a completed week (`--week N`). | `actuals` + `stats` rows; `player_week` view returns proj, act, delta | `sqlite3 nfl.db "select * from player_week where week=N order by delta desc limit 5"` |
| 9 | Three LaunchAgents + `nfl-serve` installed, loaded. | `launchctl list \| grep nfl` shows 4 | ssh + that command |
| 10 | `serve.py` answering on Tailscale. | `curl http://100.126.114.42:8094/health` = 200 from the PC; `/status` with key shows last runs | curl from the PC |
| 11 | `SCOPE_OF_WORK.md` entry with what shipped, and `BACKLOG.md` START HERE block. | both files updated, committed, pushed | `git log origin/main -3` |

After 11 the manager seat hands the Edwin side (tool `nfl`, remove MLB loops) to the edwin
session. Not before: Edwin gets a live URL, not a promise.
