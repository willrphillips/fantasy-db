"""Shared plumbing for the NFL pipeline: paths, secrets, retries, run bookkeeping.

Python 3.9 on the Mac. Stdlib plus `requests` only. No AI calls anywhere.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(os.environ.get("NFL_REPO") or Path(__file__).resolve().parent.parent)
SECRETS = Path(os.environ.get("NFL_SECRETS") or REPO / ".secrets")
DB_PATH = Path(os.environ.get("NFL_DB") or REPO / "nfl.db")
ET = ZoneInfo("America/New_York")

LEAGUE_ID = int(os.environ.get("NFL_LEAGUE_ID", "206739"))
WILL_TEAM_ID = int(os.environ.get("NFL_WILL_TEAM_ID", "1"))

RETRIES = 3
RETRY_GAP_S = int(os.environ.get("NFL_RETRY_GAP_S", "300"))

log = logging.getLogger("nfl")
if not log.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    log.addHandler(_h)
    log.setLevel(os.environ.get("NFL_LOG", "INFO"))


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def today_et():
    return datetime.now(ET).date()


def utc_to_et_date(iso_utc: str):
    """'2026-09-13T17:00:00Z' -> date in ET."""
    dt = datetime.strptime(iso_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return dt.astimezone(ET).date()


def read_secret_json(name: str) -> dict:
    p = SECRETS / name
    if not p.exists():
        raise FileNotFoundError(f"missing secret file {p}; copy {p}.example and fill it in")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write_secret_json(name: str, data: dict) -> None:
    p = SECRETS / name
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def read_secret_text(name: str) -> str:
    p = SECRETS / name
    if not p.exists():
        raise FileNotFoundError(f"missing secret file {p}")
    return p.read_text(encoding="utf-8").strip()


def retry(fn, *args, what: str = "", attempts: int = RETRIES, gap: int = RETRY_GAP_S, **kw):
    """Call fn(*args, **kw); on exception wait `gap` seconds and try again, `attempts` total."""
    last = None
    for i in range(1, attempts + 1):
        try:
            return fn(*args, **kw)
        except Exception as e:  # noqa: BLE001
            last = e
            log.warning("%s attempt %d/%d failed: %s",
                        what or getattr(fn, "__name__", "call"), i, attempts, e)
            if i < attempts:
                time.sleep(gap)
    raise last


class Run:
    """Context manager that opens and closes a `runs` row. A crash still closes it with ok=0."""

    def __init__(self, conn, job: str):
        self.conn = conn
        self.job = job
        self.id = None
        self.rows = 0
        self.error = None
        self.ok = 1

    def __enter__(self):
        cur = self.conn.execute(
            "INSERT INTO runs (job, started_at) VALUES (?, ?)", (self.job, now_utc())
        )
        self.conn.commit()
        self.id = cur.lastrowid
        log.info("run %s #%s started", self.job, self.id)
        return self

    def __exit__(self, et, ev, tb):
        if et is not None:
            self.ok = 0
            self.error = "".join(traceback.format_exception(et, ev, tb))[-4000:]
        self.conn.execute(
            "UPDATE runs SET ended_at=?, ok=?, rows=?, error=? WHERE id=?",
            (now_utc(), self.ok, self.rows, self.error, self.id),
        )
        self.conn.commit()
        tail = self.error.strip().splitlines()[-1] if self.error else ""
        log.info("run %s #%s ended ok=%s rows=%s %s", self.job, self.id, self.ok, self.rows, tail)
        return False
