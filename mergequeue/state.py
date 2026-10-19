"""The FIFO: one SQLite file under the (git-ignored) data dir."""
from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,      -- FIFO order
    pr INTEGER NOT NULL UNIQUE,
    status TEXT NOT NULL,                      -- queued | running
    trigger TEXT NOT NULL,                     -- label | comment | cli
    head_sha TEXT, mq_sha TEXT, base_sha TEXT,
    run_id INTEGER, run_url TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    enqueued_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS outcomes (pr INTEGER PRIMARY KEY, ts TEXT NOT NULL, outcome TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS log (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, pr INTEGER, msg TEXT NOT NULL);
"""


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class Queue:
    def __init__(self, path: Path):
        self.path = path
        with closing(self._c()) as c:
            c.executescript(SCHEMA)

    def _c(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=10000")
        return c

    def log(self, pr: int | None, msg: str) -> None:
        print(f"mq: {'#%d ' % pr if pr else ''}{msg}", flush=True)
        with closing(self._c()) as c:
            c.execute("INSERT INTO log(ts,pr,msg) VALUES(?,?,?)", (now(), pr, msg))

    def enqueue(self, pr: int, trigger: str) -> bool:
        with closing(self._c()) as c:
            try:
                c.execute("INSERT INTO entries(pr,status,trigger,enqueued_at) VALUES(?,?,?,?)",
                          (pr, "queued", trigger, now()))
            except sqlite3.IntegrityError:
                return False
        return True

    def get(self, pr: int) -> dict[str, Any] | None:
        with closing(self._c()) as c:
            r = c.execute("SELECT * FROM entries WHERE pr=?", (pr,)).fetchone()
        return dict(r) if r else None

    def by_run(self, run_id: int) -> dict[str, Any] | None:
        with closing(self._c()) as c:
            r = c.execute("SELECT * FROM entries WHERE run_id=?", (run_id,)).fetchone()
        return dict(r) if r else None

    def all(self) -> list[dict[str, Any]]:
        with closing(self._c()) as c:
            return [dict(r) for r in c.execute("SELECT * FROM entries ORDER BY id")]

    def running(self) -> dict[str, Any] | None:
        with closing(self._c()) as c:
            r = c.execute("SELECT * FROM entries WHERE status='running' ORDER BY id LIMIT 1").fetchone()
        return dict(r) if r else None

    def head(self) -> dict[str, Any] | None:
        with closing(self._c()) as c:
            r = c.execute("SELECT * FROM entries WHERE status='queued' ORDER BY id LIMIT 1").fetchone()
        return dict(r) if r else None

    def update(self, pr: int, **fields: Any) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        with closing(self._c()) as c:
            c.execute(f"UPDATE entries SET {cols} WHERE pr=?", (*fields.values(), pr))

    def finish(self, pr: int, outcome: str) -> None:
        """Remove an entry and remember when/how it ended (stops a stale /queue comment re-queueing it)."""
        with closing(self._c()) as c:
            c.execute("DELETE FROM entries WHERE pr=?", (pr,))
            c.execute("INSERT INTO outcomes(pr,ts,outcome) VALUES(?,?,?) ON CONFLICT(pr) DO UPDATE SET "
                      "ts=excluded.ts, outcome=excluded.outcome", (pr, now(), outcome))

    def last_outcome_ts(self, pr: int) -> str | None:
        with closing(self._c()) as c:
            r = c.execute("SELECT ts FROM outcomes WHERE pr=?", (pr,)).fetchone()
        return r["ts"] if r else None

    def recent_log(self, n: int = 10) -> list[dict[str, Any]]:
        with closing(self._c()) as c:
            return [dict(r) for r in c.execute("SELECT * FROM log ORDER BY id DESC LIMIT ?", (n,))][::-1]
