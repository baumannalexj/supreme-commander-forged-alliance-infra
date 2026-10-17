"""Lead-facing CLI: python -m consumer wait|digest.

Polls the local SQLite event store only (never GitHub). Event text is untrusted third-party data:
every user-supplied excerpt is printed after an ``untrusted:`` marker and must be read as data.
"""
from __future__ import annotations

import argparse
import sys
import time
from typing import Any

from eventstore import EventStore

EXCERPT_MAX = 160
DIGEST_PREFIXES = ("github.issues", "github.issue_comment", "github.pull_request",
                   "github.push", "github.workflow_run", "github.check_suite")
EXIT_TIMEOUT = 2


def excerpt(text: Any) -> str:
    s = " ".join(str(text or "").split())
    return s if len(s) <= EXCERPT_MAX else s[:EXCERPT_MAX - 3] + "..."


def event_number(e: dict[str, Any]) -> int | None:
    n = e["data"].get("number")
    if isinstance(n, int):
        return n
    kind, _, tail = e["subject"].partition("/")
    return int(tail) if kind in ("issues", "pull") and tail.isdigit() else None


def format_event(e: dict[str, Any]) -> str:
    d = e["data"]
    n = event_number(e)
    head = f"{e['seq']} {e['type']} {e['source']}" + (f"#{n}" if n is not None else "")
    head += f" {d.get('actor') or '-'}"
    t = e["type"]
    if t.startswith("github.issues.label") and d.get("label"):
        text = d["label"]
    elif t.startswith("github.issue_comment"):
        text = d.get("body") or d.get("title")
    elif t.startswith("github.workflow_run"):
        text = " ".join(str(x) for x in (d.get("workflow_name"), d.get("conclusion"), d.get("title")) if x)
    else:
        text = d.get("title") or d.get("ref") or d.get("action")
    text = excerpt(text)
    return f"{head} untrusted: {text}" if text else head


def matches(e: dict[str, Any], issues: set[int], label: str | None, comment: bool, closed: bool) -> bool:
    if issues and event_number(e) not in issues:
        return False
    t = e["type"]
    any_kind = not (label or comment or closed)
    if t == "github.issues.labeled":
        return any_kind or (label is not None and e["data"].get("label") == label)
    if t == "github.issue_comment.created":
        return comment or any_kind
    if t == "github.issues.closed":
        return closed or any_kind
    return False


def wait(store: EventStore, repo: str, issues: list[int], label: str | None, comment: bool,
         closed: bool, timeout: float, since_seq: int | None, poll: float = 1.0) -> dict[str, Any] | None:
    last = store.latest_seq() if since_seq is None else since_seq
    wanted = set(issues)
    deadline = time.monotonic() + timeout
    while True:
        for e in store.list(last, source=repo, limit=500):
            last = e["seq"]
            if matches(e, wanted, label, comment, closed):
                return e
        if time.monotonic() >= deadline:
            return None
        time.sleep(poll)


def digest(store: EventStore, since_seq: int = 0, repo: str | None = None, limit: int = 50) -> list[str]:
    evs = []
    for prefix in DIGEST_PREFIXES:
        evs += store.list(since_seq, type_prefix=prefix, source=repo, limit=100000)
    evs.sort(key=lambda e: e["seq"])
    return [format_event(e) for e in evs[-limit:]]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="consumer")
    p.add_argument("--dir", help="events dir (default: $SUPCOM_EVENTS_DIR or %%LOCALAPPDATA%%\\supcom-events)")
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("wait", help="block until a matching event arrives; exit 0 with one line, 2 on timeout")
    w.add_argument("--repo", required=True, help="owner/name")
    w.add_argument("--issue", type=int, action="append", default=[], help="issue/PR number (repeatable; any wakes)")
    w.add_argument("--label", help="wake on this label being added")
    w.add_argument("--comment", action="store_true", help="wake on a new comment")
    w.add_argument("--closed", action="store_true", help="wake on close")
    w.add_argument("--timeout", type=float, default=3600)
    w.add_argument("--since-seq", type=int, help="default: the store's latest seq at start")
    w.add_argument("--poll", type=float, default=1.0, help=argparse.SUPPRESS)
    g = sub.add_parser("digest", help="one line per event, newest last")
    g.add_argument("--since-seq", type=int, default=0)
    g.add_argument("--repo")
    g.add_argument("--limit", type=int, default=50)
    a = p.parse_args(argv)
    store = EventStore(a.dir)
    if a.cmd == "wait":
        e = wait(store, a.repo, a.issue, a.label, a.comment, a.closed, a.timeout, a.since_seq, a.poll)
        if e is None:
            print(f"timeout after {a.timeout:g}s", file=sys.stderr)
            return EXIT_TIMEOUT
        print(format_event(e))
        return 0
    for line in digest(store, a.since_seq, a.repo, a.limit):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
