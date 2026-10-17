"""Tool handlers for the supcom-events MCP server (SDK-free so they can be tested directly).

Results are compact: no raw webhook payloads, and free text is truncated.
All event content is untrusted data written by third parties, never instructions.
"""
from __future__ import annotations

from typing import Any

from eventstore import EventStore
from eventstore.store import check_consumer

LIST_MAX = 50
NEXT_MAX = 20
COMPACT_TEXT = 300  # per string field in list/next results; get_event returns up to 4000


def _clip(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit]
    return value


def view(event: dict[str, Any], full: bool = False) -> dict[str, Any]:
    data = event["data"] if full else {k: _clip(v, COMPACT_TEXT) for k, v in event["data"].items()}
    return {
        "seq": event["seq"], "id": event["id"], "source": event["source"],
        "type": event["type"], "time": event["time"], "subject": event["subject"],
        "data": data,
    }


def _limit(limit: int, maximum: int) -> int:
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1 or limit > maximum:
        raise ValueError(f"limit must be an integer in 1..{maximum}")
    return limit


def list_events(store: EventStore, since_seq: int = 0, type_prefix: str | None = None,
                source: str | None = None, limit: int = 20) -> dict[str, Any]:
    if not isinstance(since_seq, int) or since_seq < 0:
        raise ValueError("since_seq must be an integer >= 0")
    events = store.list(since_seq, type_prefix or None, source or None, _limit(limit, LIST_MAX))
    return {"count": len(events), "events": [view(e) for e in events],
            "last_seq": events[-1]["seq"] if events else since_seq}


def get_event(store: EventStore, id: str) -> dict[str, Any]:
    event = store.get(id)
    return {"found": event is not None, "event": view(event, full=True) if event else None}


def next_events(store: EventStore, consumer: str, limit: int = 10) -> dict[str, Any]:
    check_consumer(consumer)
    before, events, after = store.next_events(consumer, _limit(limit, NEXT_MAX))
    return {"consumer": consumer, "cursor_before": before, "cursor_after": after,
            "count": len(events), "events": [view(e) for e in events]}


def ack(store: EventStore, consumer: str, seq: int) -> dict[str, Any]:
    check_consumer(consumer)
    latest = store.latest_seq()
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0 or seq > latest:
        raise ValueError(f"seq must be an integer in 0..{latest}")
    store.set_cursor(consumer, seq)
    return {"consumer": consumer, "last_seq": seq}


def stats(store: EventStore) -> dict[str, Any]:
    return store.stats()
