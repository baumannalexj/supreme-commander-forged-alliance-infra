"""SQLite-backed event bus.

Tables:
  events(seq, id, source, type, time, subject, data_json, received_at)
  cursors(consumer, last_seq)

``seq`` is a monotonically increasing integer assigned on insert; consumers
track the highest seq they have seen. ``id`` (the GitHub delivery id) is
unique, which makes appends idempotent.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Any

ENV_DIR = "SUPCOM_EVENTS_DIR"
CONSUMER_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    id          TEXT NOT NULL UNIQUE,
    source      TEXT NOT NULL,
    type        TEXT NOT NULL,
    time        TEXT NOT NULL,
    subject     TEXT NOT NULL,
    data_json   TEXT NOT NULL,
    received_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS events_type ON events(type);
CREATE INDEX IF NOT EXISTS events_source ON events(source);
CREATE TABLE IF NOT EXISTS cursors (
    consumer TEXT PRIMARY KEY,
    last_seq INTEGER NOT NULL DEFAULT 0
);
"""


def default_events_dir() -> Path:
    env = os.environ.get(ENV_DIR)
    if env:
        return Path(env)
    local = os.environ.get("LOCALAPPDATA")
    if local:
        return Path(local) / "supcom-events"
    return Path.home() / ".local" / "share" / "supcom-events"


def utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def check_consumer(name: str) -> None:
    if not isinstance(name, str) or not CONSUMER_RE.match(name):
        raise ValueError("consumer must match ^[a-z0-9][a-z0-9_.-]{0,63}$")


def _row_to_event(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "seq": row["seq"],
        "id": row["id"],
        "source": row["source"],
        "type": row["type"],
        "time": row["time"],
        "subject": row["subject"],
        "data": json.loads(row["data_json"]),
        "received_at": row["received_at"],
    }


class EventStore:
    def __init__(self, directory: str | os.PathLike | None = None):
        self.dir = Path(directory) if directory else default_events_dir()
        self.dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.dir / "events.sqlite"
        self.jsonl_path = self.dir / "events.jsonl"
        with closing(self._connect()) as con:
            con.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=10000")
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    # -- writes ---------------------------------------------------------
    def append(self, event: dict[str, Any]) -> tuple[bool, int]:
        """Insert an envelope. Returns (inserted, seq); inserted False on duplicate id."""
        received_at = utc_now()
        with closing(self._connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                row = con.execute("SELECT seq FROM events WHERE id=?", (event["id"],)).fetchone()
                inserted = row is None
                if inserted:
                    cur = con.execute(
                        "INSERT INTO events(id, source, type, time, subject, data_json, received_at)"
                        " VALUES (?,?,?,?,?,?,?)",
                        (
                            event["id"], event["source"], event["type"], event["time"],
                            event["subject"], json.dumps(event["data"], separators=(",", ":")),
                            received_at,
                        ),
                    )
                    seq = cur.lastrowid
                else:
                    seq = row["seq"]
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
        if inserted:
            mirror = dict(event, seq=seq, received_at=received_at)
            with open(self.jsonl_path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(mirror, separators=(",", ":")) + "\n")
        return inserted, seq

    def set_cursor(self, consumer: str, seq: int) -> None:
        check_consumer(consumer)
        with closing(self._connect()) as con:
            con.execute(
                "INSERT INTO cursors(consumer,last_seq) VALUES(?,?)"
                " ON CONFLICT(consumer) DO UPDATE SET last_seq=excluded.last_seq",
                (consumer, seq),
            )

    # -- reads ----------------------------------------------------------
    def get(self, event_id: str) -> dict[str, Any] | None:
        with closing(self._connect()) as con:
            row = con.execute("SELECT * FROM events WHERE id=?", (event_id,)).fetchone()
        return _row_to_event(row) if row else None

    def list(self, since_seq: int = 0, type_prefix: str | None = None,
             source: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT * FROM events WHERE seq > ?"
        args: list[Any] = [since_seq]
        if type_prefix:
            sql += " AND substr(type,1,?) = ?"
            args += [len(type_prefix), type_prefix]
        if source:
            sql += " AND source = ?"
            args.append(source)
        sql += " ORDER BY seq ASC LIMIT ?"
        args.append(limit)
        with closing(self._connect()) as con:
            return [_row_to_event(r) for r in con.execute(sql, args)]

    def cursor(self, consumer: str) -> int:
        check_consumer(consumer)
        with closing(self._connect()) as con:
            row = con.execute("SELECT last_seq FROM cursors WHERE consumer=?", (consumer,)).fetchone()
        return row["last_seq"] if row else 0

    def next_events(self, consumer: str, limit: int = 20) -> tuple[int, list[dict[str, Any]], int]:
        """Return (cursor_before, events after the cursor, cursor_after); advances the cursor atomically."""
        check_consumer(consumer)
        with closing(self._connect()) as con:
            con.execute("BEGIN IMMEDIATE")
            try:
                row = con.execute("SELECT last_seq FROM cursors WHERE consumer=?", (consumer,)).fetchone()
                before = row["last_seq"] if row else 0
                events = [_row_to_event(r) for r in con.execute(
                    "SELECT * FROM events WHERE seq > ? ORDER BY seq ASC LIMIT ?", (before, limit))]
                after = events[-1]["seq"] if events else before
                con.execute(
                    "INSERT INTO cursors(consumer,last_seq) VALUES(?,?)"
                    " ON CONFLICT(consumer) DO UPDATE SET last_seq=excluded.last_seq",
                    (consumer, after),
                )
                con.execute("COMMIT")
            except Exception:
                con.execute("ROLLBACK")
                raise
        return before, events, after

    def latest_seq(self) -> int:
        with closing(self._connect()) as con:
            return con.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]

    def stats(self) -> dict[str, Any]:
        with closing(self._connect()) as con:
            total = con.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            latest = con.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
            by_type = {r[0]: r[1] for r in con.execute("SELECT type, COUNT(*) FROM events GROUP BY type ORDER BY type")}
            by_source = {r[0]: r[1] for r in con.execute("SELECT source, COUNT(*) FROM events GROUP BY source ORDER BY source")}
            consumers = {r[0]: r[1] for r in con.execute("SELECT consumer, last_seq FROM cursors ORDER BY consumer")}
        return {"total": total, "latest_seq": latest, "by_type": by_type,
                "by_source": by_source, "consumers": consumers}
