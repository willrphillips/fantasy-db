# BACKLOG — fantasy-db

## START HERE (2026-09-15)

| | |
|---|---|
| **State** | NFL pipeline LIVE on old-will-macbook. MLB retired 2026-09-14. |
| **Where** | `~/fantasy-db` on the Mac (Tailscale `100.126.114.42`), venv on system Python 3.9, `nfl.db` beside it. Nothing on atlas, nothing on the PC. |
| **Jobs** | launchd: `nfl-weekly` Tue 05:00, `nfl-am` 07:00 game days, `nfl-pm` 02:00 after games, `nfl-serve` always. `launchctl list \| grep nfl` shows 4. |
| **Read path** | `http://100.126.114.42:8094` with `Authorization: Bearer <~/fantasy-db/.secrets/serve-key>`; `/health` needs no key. Endpoints in `nfl/serve.py` docstring. |
| **Is it healthy?** | `curl -H "Authorization: Bearer $KEY" http://100.126.114.42:8094/status` shows the last am / pm / weekly run with `ok` and `rows`. `ok=0` + `error='cookie'` means the Yahoo cookie died. |
| **Secrets** | `.secrets/yahoo_cookie.txt` is the one that matters. Refresh: copy the `Cookie:` header from a logged-in Chrome request to football.fantasysports.yahoo.com into that file. |
| **Spec + log** | `NFL_PLAN.md` (amended 2026-09-15), `SCOPE_OF_WORK.md` (2026-09-15 entry). |
| **Next** | Edwin handover: tool `nfl`, key in `~/.nfl-key` on atlas, remove the dead MLB loops from `bot.py`. Manager seat owns that handover. |
| **Intended end state** | Will submitted the Yahoo Fantasy API application 2026-09-15. When approved, the read-only OAuth scope (`nfl/yahoo_api.py`, already written) replaces the cookie for rosters, actuals and settings. A session cookie can write; a read scope cannot. That was the original reason for the API and it stays the target. The cookie remains for projections only, which the API does not expose. |

## Queue

1. **Edwin `nfl` tool** (edwin repo, separate session). Wrap the eight endpoints, same shape as `~/bin/gob`. Refuse to answer fantasy questions from anything else.
2. **Cookie death detection.** Today it is silent until `/status` is read. Options: `nfl-serve` could add a `stale` flag when the last am/pm has `error='cookie'`; or Edwin's morning check asks `/status`. No Discord from the pipeline itself (plan rule).
3. **Yahoo API application.** Will submitted 2026-09-15. On approval, swap `weekly/am/pm` to `nfl/yahoo_api.py` for rosters, actuals and settings (read-only scope, cannot write, unlike the cookie); keep `yahoo_web.py` for projections only. This is the intended end state, see START HERE.
4. **Kicker gsis_id gaps.** 10 kickers unresolved because nflverse rosters lack them. Harmless (no nflverse stats for them either); revisit if a K matters.
5. **Snap counts.** `stats.snaps` is NULL. nflverse `snap_counts_2026.csv` keys on pfr_id; the roster file carries pfr_id, so the join is possible. Not needed yet.
6. **Intra-week projection drift.** `projections` keeps one row per player per week (last pull wins). Add `pulled_at` to the key if Will ever asks how a projection moved.
7. **Week boundary.** `nfl-pm` decides "yesterday had a game" from `games.kickoff_utc`; a Saturday game in week 15+ is covered, a rescheduled game is not until `weekly` refreshes the schedule on Tuesday.
8. **MLB cleanup.** The retired MLB scripts still sit at the repo root. Move to `mlb/` or delete once the 2026-09-14 restore window is judged closed.
