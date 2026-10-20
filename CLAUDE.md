# fantasy-db: Claude project notes

Read this first. Repo is **`willrphillips/fantasy-db`** on GitHub; the local folder is
`C:\Code\fantasy-db` on the PC and `~/fantasy-db` on old-will-macbook. One name everywhere.

Global rules: ~/.claude/CLAUDE.md and ~/.claude/AI_CHARTER.md bind here; this file holds
project specifics only.

## Status (2026-10-20)

| Season | State | Where it runs | Spec |
|---|---|---|---|
| NFL 2026 | **LIVE since 2026-09-15**; `:8094/health` answered `ok` on 2026-10-20 | old-will-macbook (Tailscale `100.126.114.42`), launchd, zero AI tokens | `NFL_PLAN.md` |
| MLB 2026 | **RETIRED 2026-09-14** | nothing runs; atlas checkout kept, timers disabled | `docs/MLB_2026_RETIRED.md` |

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
- **Em dashes:** global `CLAUDE.md` section 6 governs.

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

Force-match GitHub, discarding local edits: `git fetch origin && git reset --hard origin/main`,
only after `git status` shows nothing another session needs.

The MLB published-data repo (`willrphillips/fantasy-snapshots`) is separate, frozen, and
never pulled here.

## How code reaches the Mac

`~/fantasy-db` on old-will-macbook is a git clone of this repo (`NFL_PLAN.md` task 4).
Commit and push from the PC, then on the Mac (`ssh willrphillips@100.126.114.42`):
`cd ~/fantasy-db && git pull origin main`. The am/pm/weekly jobs pick up new code on
their next run; `nfl-serve` stays running and needs
`launchctl kickstart -k gui/$(id -u)/com.willr.nfl-serve`. Secrets and `nfl.db` exist
only on the Mac and never travel through git.

## MLB 2026 (retired)

The retired MLB pipeline is recorded in `docs/MLB_2026_RETIRED.md`. Nothing in it runs.

### File location rule

Save all files inside this project folder (this directory or its subfolders). Do NOT save to Downloads, `C:\Users\willr\`, or any location outside this project. If saving elsewhere is truly required, STOP and confirm with Will first that it is the best choice for the job.
