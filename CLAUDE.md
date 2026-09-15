# fantasy-db — Claude project notes

Read this first. Repo is **`willrphillips/fantasy-db`** on GitHub; the local folder is
`C:\Code\fantasy-db` on the PC and `~/fantasy-db` on old-will-macbook. One name everywhere.

## Status (2026-09-14)

| Season | State | Where it runs | Spec |
|---|---|---|---|
| NFL 2026 | **LIVE since 2026-09-15** | old-will-macbook (Tailscale `100.126.114.42`), launchd, zero AI tokens | `NFL_PLAN.md` |
| MLB 2026 | **RETIRED 2026-09-14** | nothing runs; atlas checkout kept, timers disabled | section at the bottom of this file |

`NFL_PLAN.md` is the spec for the NFL work: fixed facts, schema, the three launchd jobs, the
Edwin read path on `:8094`, and the 11-task handover checklist. Decisions there are made; do
not reopen them. `SCOPE_OF_WORK.md` is the dated decision log; `BACKLOG.md` opens with a
START HERE block.

## Operating preferences (how to respond)

These apply to every session, both Claude Code and Claude Chat.

- **Timestamp every reply.** Begin each response with a short timestamp
  in US Eastern time, e.g. `[2026-09-14 7:21 PM EDT]` (EDT in summer,
  EST in winter; let the clock decide).
- **Lead with the recommendation, then the numbers.** Be brief. Give the
  call first, justify with data after. No hedging, no fantasy-is-random disclaimers.
- **Confirm everything; never fabricate.** Ground every number in `nfl.db` (or, for the
  retired MLB side, `fantasy.db`). Do NOT use live web stats to value players; the NFL
  pipeline stores Yahoo's own projections and actuals, and those are the numbers that
  score. If a number cannot be confirmed from the db, say so.
- **No em dashes in anything that ships** (docs, READMEs, Discord copy). Use a comma,
  colon, or two sentences.

## NFL layout (target; see `NFL_PLAN.md` for the full table)

| Path | What |
|---|---|
| `nfl/db.py` | schema + `python -m nfl.db init` (idempotent) |
| `nfl/yahoo_web.py` | login cookie; the whole Yahoo read path: teams, settings, rosters, projected and actual points |
| `nfl/yhtml.py` | depth-aware HTML table parser (Yahoo nests tables inside cells) |
| `nfl/yahoo_api.py` | RETIRED: OAuth2 client for the gated Fantasy API, kept for the day the application is approved |
| `nfl/nflverse.py` | schedules, player_stats, rosters CSVs |
| `nfl/am.py` / `nfl/pm.py` / `nfl/weekly.py` | the three launchd jobs |
| `nfl/serve.py` | read-only HTTP on `100.126.114.42:8094` for Edwin |
| `.secrets/` | gitignored; `yahoo_cookie.txt` (the live secret), `serve-key`, `yahoo.json` (retired API creds). Real files live only on the Mac. |
| `nfl.db` | gitignored; lives on the Mac |

Rules that bind every NFL script: every run writes a `runs` row (crash included);
3 retries 5 minutes apart inside the script; no Discord, no Edwin, no AI calls.

## Local workflow & syncing (VS Code)

```bash
git status                 # check for local edits first
git stash                  # ONLY if status shows uncommitted changes
git pull origin main
git stash pop              # only if you stashed
```

Force-match GitHub, discarding local edits: `git fetch origin && git reset --hard origin/main`.

The MLB published-data repo (`willrphillips/fantasy-snapshots`) is separate, frozen, and
never pulled here.

---

# RETIRED 2026-09-14: the MLB pipeline

Everything below describes the MLB season and is kept as the record. Nothing in it runs.
The atlas timers were disabled and the `bot.py` loops retired on 2026-09-15 03:16 UTC
(`SCOPE_OF_WORK.md`); the checkout and `fantasy.db` were left in place. Reversal is
`sudo /usr/local/sbin/fantasy-restore.sh` on atlas. The MLB-specific response rules
(Strategy C: punt SV + SB, prioritise HR/RBI, protect W/ERA/WHIP/K; trust the ESPN app for
position eligibility; quantify moves with `playoff_odds.py`) applied to that season only.

### What this is

A data layer for ESPN fantasy baseball. The Hetzner box **atlas-cloud**
pulls MLB Stats API + Baseball Savant + ESPN league state every night and
stores a daily season-to-date snapshot per player. `fantasy.db` plus the
pre-baked markdown views are published to a public GitHub repo
(`willrphillips/fantasy-snapshots`) every morning. From there, both Claude
Chat and Claude Code can read the data without auth.

**Who owns this: Edwin.** The runtime is his, at
its atlas checkout (path in `SCOPE_OF_WORK.md` 2026-09-14; the symlink is gone), under
his own venv. Analysis and roster moves are his to make unprompted; trades
and anything spending money wait for Will. The iMac "Cocky-Claude" ran all
of this until **2026-07-21** and is historical from that date, not current:
see `MIGRATION_2026-07-21.md`.

The owner of the league is "Captain Phillips" (team_id=9, league_id
2057904545, season=2026, 10-team head-to-head categories).

### Data flow

All times ET. Two different schedulers, and the difference matters.

**systemd timers on atlas-cloud** (`systemctl list-timers 'fantasy-*'`):

```
3:30  mlb_ingest.py     -> fantasy.db rows for every tracked player
4:30  views.py          -> public/views/*.md
4:45  anomaly.py        -> public/views/anomaly_digest.md
6:00  health_check.py   -> independent watchdog
```

**In-process loops inside Edwin's `bot.py`**, which no timer list will show:

```
4:00       nightly_advisor.py -> the morning brief
5:07       db_publish.py      -> gzipped db + views to GitHub
every 30m  roster_triage.py   -> in-game lineup fixes, from 30 min before the
                                 day's first pitch until the last game is final
```

If you are asking "is job X scheduled?", `list-timers` answers only half the
question. Check `fantasy_*_loop()` in `bot.py` for the other half.

Failures post to Discord (`notify.py`), throttled to one alert per script per
day via `<atlas checkout>/.alert_state`. Email alerting was retired 2026-07-21;
`send_email` is `false` in the live config.

The roster triage has an on/off switch: `/triage status|on|off|now` in Discord,
or write `on`/`off` into `~/codex/edwin/state/fantasy-triage-enabled.txt`. A
missing file means on. `journalctl -u edwin.service | grep fantasy-triage` shows
every run, and an empty result on a game day means it is not running.

### Universe

Tracked players = every active MLB player from the season-roster index
(`/api/v1/sports/1/players?season=2026`, ~1100 players) UNIONed with
anyone on a Captain Phillips roster or in the top-200 ESPN FA pool
within the last 30 days. The pipeline covers more than just the
fantasy league; you can query any active MLB player.

### Schema (fantasy.db)

| Table | What it holds |
|---|---|
| `players` | bio per player (`mlb_id` PK, `name`, `team`, `primary_pos`, `last_tracked`) |
| `hitting_stats` | one row per (player, date) — **season-to-date** as of that date |
| `pitching_stats` | one row per (player, date) — **season-to-date** as of that date |
| `statcast` | season-to-date Statcast snapshot per (player, date, side) |
| `rosters` | snapshot of every ESPN team's roster, one row per (date, team, player) |
| `standings` | one row per (date, team) |
| `matchups` | one row per (date, period, home, away, category) |
| `fa_pool` | top-200 free agents per (date, player) |
| `id_map` | ESPN `playerId` → MLBAM `mlb_id` crosswalk + resolution cache |
| `pull_log` | one row per ingest run (mode, duration, counts, errors) |

**Critical:** `hitting_stats` and `pitching_stats` are
**cumulative**, not per-game. To get a window (L7, L14, L30, custom),
**subtract two snapshots** — today's row minus the row N days ago.
`fantasy_lib.window_stats(name, days=14)` does this for you.

`statcast` is current-snapshot only. Savant doesn't expose historical
daily data, so the statcast time series builds forward from
2026-05-21 (the day the BOM bug was fixed; earlier rows are absent
or were garbage and have been deleted).

### How to query

```python
import os
os.environ["FANTASY_DB"] = "/path/to/fantasy.db"   # downloaded copy
from fantasy_lib import (
    my_roster, roster, standings, matchups, fa_pool_latest,
    window_stats, windows_all, player, player_history,
    hot_bats, hot_arms, cold_bats,
    regression_watch, fip_era_gap,
    trade_scout, teams_list,
    health, latest_date, latest_roster_date,
)

my_roster()                                  # Captain Phillips
window_stats("Juan Soto", days=14)           # L14 by subtraction
hot_bats(days=14, n=20, fa_only=True)        # waiver targets
trade_scout("Bay County Buccaneers", sort="hr")
regression_watch("up", n=15)                 # xwOBA - wOBA gaps
health()                                     # freshness + row counts
```

`fantasy_lib` honors the `FANTASY_DB` env var so the same code works on
atlas-cloud (live db) or on any machine with a downloaded copy.

### Three defects fixed (load-bearing — don't undo)

These were latent in the original chat-built code and were corrected on
2026-05-19. Any future change that touches the same areas must respect
the constraints below.

1. **`load_league()` requires `cfg["league_id"]`.** config.json didn't
   originally have the key, which made the script hard-crash. The fix
   added a `LEAGUE_ID_FALLBACK` constant and a try/except that returns
   `None` instead of raising on any missing key. Don't go back to
   raw subscript access for required cfg fields.

2. **ESPN `playerId` is not the MLBAM id.** The original code stored
   ESPN ids in `rosters.mlb_id`, then fed them to the MLB Stats API
   (404s) and joined them against statcast (which uses real MLB ids).
   The fix introduced the `id_map` crosswalk and a resolver
   (`resolve_mlb_id`) that uses the MLB season-roster index with team
   disambiguation. **Policy: ambiguous or no-match leaves `mlb_id`
   NULL and logs a WARNING.** Never silently mis-map. The watchdog
   surfaces roster gaps explicitly. The 13 unresolved players are
   injured/suspended FAs absent from the 2026 season index; they
   self-heal when they play.

3. **Date cadence is per-table.** Fantasy-state tables (`rosters`,
   `fa_pool`, `standings`, `matchups`) are tagged with the run date
   (today). Stat tables are tagged with the snapshot date (yesterday
   for nightly). `fantasy_lib` exposes `latest_roster_date()`,
   `latest_fa_date()`, `latest_standings_date()`,
   `latest_matchup_date()`, and `latest_date()` (the stats max). Use
   the right anchor for each query. `trade_scout` joins rosters at
   `latest_roster_date()` and stats at `latest_date()`.

### Other constraints to know

- **Savant CSV has a UTF-8 BOM** that breaks `csv.DictReader` quoted-
  field parsing. `fetch_savant_csv` strips it. If you ever change the
  Savant pull, keep the strip.
- **Alert subjects and bodies are ASCII-only.** Historical, from the email
  era: `espn_utils.send_email` used `smtplib.sendmail(msg.as_string())`,
  which fails on non-ASCII in the Subject header. Alerts go to Discord now
  and the constraint no longer binds, but the function is still there and
  still has the flaw if anything ever calls it again.
- **A 200 from ESPN is not proof the change happened.** ESPN freezes a
  player's roster slot the moment his game starts, answers the move `200`,
  and silently ignores it. `set_lineup` and `add_drop` now read the roster
  back and return `applied` / `stuck` / `verified` / `pending`; believe
  those, never bare `ok`. A `WAIVER` claim is legitimately pending and
  cannot be confirmed by a read-back; a `FREEAGENT` add can. Fixed
  2026-08-30 after the triage spent an evening announcing a swap of two
  players whose games were already final.
- **The MLB schedule is cached once per calendar day.** `fetch_schedule()`
  writes `cache/schedule_YYYY-MM-DD.json` and reuses it, so a copy written
  by the 4am job says every game is still `Preview` at 9pm. Anything that
  cares whether a game has started must pass `force_refresh=True`.
- **Statcast inserts are `INSERT OR REPLACE` on UNIQUE(mlb_id, date,
  side).** A bad `mlb_id` value (e.g., the year "2026") will collapse
  every row of a side into one. If statcast row count looks tiny,
  check column extraction.
- **Matchup ingest reads `value`, not `score`, and `leader` comes from
  ESPN's `result` field.** espn_api box scores are `{CAT: {"value":
  float, "result": "WIN"|"LOSS"|"TIE"|None}}`. `fetch_fantasy_state`
  reads `value`, sets `leader` from the home `result` (so lower-is-
  better cats ERA/WHIP are correct — never re-derive `leader` from a raw
  value comparison), and **skips component stats** (AB/H/OUTS/ER/P_H/
  P_BB, `result=None`) so only the 11 scored cats persist. `standings`
  rank is pinned to ESPN's `team.standing`; `pct = (wins+0.5*ties)/gp`.
  Fixed 2026-06-05; don't undo.
- **fantasy.db is gitignored** in this repo. The data lives at
  `willrphillips/fantasy-snapshots`. Don't add it here.
- **`config.json` is gitignored.** It contains `espn_s2`, `swid`,
  `github_token`, `gmail_app_password`. Never commit. If it leaks,
  rotate immediately (log out of ESPN to invalidate cookies; revoke
  the GitHub token; generate a new Gmail app password).

### Public URLs

Data:
- `https://willrphillips.github.io/fantasy-snapshots/data/fantasy.db`

Views:
- `https://willrphillips.github.io/fantasy-snapshots/views/team_review.md`
- `https://willrphillips.github.io/fantasy-snapshots/views/waiver_hitters.md`
- `https://willrphillips.github.io/fantasy-snapshots/views/waiver_pitchers.md`
- `https://willrphillips.github.io/fantasy-snapshots/views/regression_watch.md`
- `https://willrphillips.github.io/fantasy-snapshots/views/trade_targets.md`
- `https://willrphillips.github.io/fantasy-snapshots/views/category_standings.md`
- `https://willrphillips.github.io/fantasy-snapshots/views/pull_status.md`
- `https://willrphillips.github.io/fantasy-snapshots/views/anomaly_digest.md`

### File map

| File | Purpose |
|---|---|
| `db_init.py` | One-time schema setup. `--reset` drops everything (destructive). |
| `mlb_ingest.py` | Daily + backfill ingest. Modes: `--backfill`, `--only-fantasy`, `--player NAME`, `--limit N`. |
| `fantasy_lib.py` | Query helper. Honors `FANTASY_DB` env var. |
| `views.py` | Generate the seven core markdown reports. |
| `anomaly.py` | Build `anomaly_digest.md` — standout single-game lines vs season baseline. Daily delta via 1-day snapshot subtraction (INNER JOIN to prior snapshot, never COALESCE-to-zero). Writes into the views dir; `db_publish` globs it. |
| `db_publish.py` | Push fantasy.db + views to GitHub via Contents API. |
| `health_check.py` | Independent watchdog. Reads only; alerts on freshness, coverage, errors, URL reachability. |
| `notify.py` | `alert(script, subject, body)`. Failure-only, throttled. |
| `espn_utils.py` | ESPN league plumbing (cookies, transactions, the retired `send_email`). |
| `fantasy_exec.py` | The write path: `set_lineup`, `add_drop`, `propose_trade`, `get_roster`, `whoami`. Portable, config-driven, dry-run by default. Read `INTEGRATION.md` before changing it. |
| `roster_triage.py` | In-game lineup fixes. Run by Edwin's `bot.py` every 30 min inside the game window, NOT by a timer. `FANTASY_TRIAGE_DRY_RUN=1` to see what it would do. |
| `nightly_advisor.py` | The 4:00 morning brief Edwin posts to Discord. |
| `daily_projections.py` | MLB schedule, probable pitchers, and per-team `has_game` / `started`. Caches once a day; pass `force_refresh=True` for live game state. |
| `espn_nightly_moves.py` | Overnight lineup + waiver pass. Scoring helpers here are shared with the triage. |
| `playoff_odds.py` | Scenario tool for quantifying a roster/trade/waiver move. |
| `set_lineup.py`, `waiver_move.py`, `apply_pending.py` | Older CLI wrappers, superseded by `fantasy_exec.py`. See `INTEGRATION.md` §6. |

### When something breaks

1. On atlas-cloud, check `<atlas checkout>/ingest.log` (or `publish.log`),
   `journalctl -u fantasy-ingest.service` and friends for the timer jobs,
   and `journalctl -u edwin.service` for the brief, the publish and the
   roster triage.
2. `pull_log` table has structured counts per run. The most recent row
   tells you what happened last night.
3. `health_check.py` is the canonical "is everything OK" command —
   prints `OK: health check passed (DATE)` and exit 0 when green.
4. If you've changed the schema, drop the db (`rm fantasy.db`),
   re-init (`python3 db_init.py`), and re-run a backfill. The
   `id_map` and `players` tables rebuild from the MLB index on the
   next ingest.


### File location rule

Save all files inside this project folder (this directory or its subfolders). Do NOT save to Downloads, `C:\Users\willr\`, or any location outside this project. If saving elsewhere is truly required, STOP and confirm with Will first that it is the best choice for the job.
