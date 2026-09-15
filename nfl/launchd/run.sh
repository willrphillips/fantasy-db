#!/bin/sh
# launchd wrapper for the NFL jobs on old-will-macbook (pattern: capcom-engine/sync.sh).
#   run.sh am | pm | weekly | serve   [extra args]
# Logs go to ~/Library/Logs/nfl-<job>.log via the plist's StandardOut/ErrorPath.
job="$1"; shift
cd "$HOME/fantasy-db" || exit 1
exec "$HOME/fantasy-db/venv/bin/python" -m "nfl.$job" "$@"
