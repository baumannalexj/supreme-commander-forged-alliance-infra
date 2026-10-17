"""Local event store: SQLite (WAL) plus a JSONL mirror, with per-consumer cursors."""
from .store import EventStore, default_events_dir

__all__ = ["EventStore", "default_events_dir"]
