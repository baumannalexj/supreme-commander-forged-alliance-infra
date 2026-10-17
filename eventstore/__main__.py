"""Human CLI: python -m eventstore tail|list|ack|stats."""
from __future__ import annotations

import argparse
import json
import sys
import time

from .store import EventStore


def _line(e: dict) -> str:
    d = e["data"]
    bits = [f"#{e['seq']}", e["time"], e["type"], e["source"], e["subject"]]
    if d.get("actor"):
        bits.append("@" + d["actor"])
    if d.get("title"):
        bits.append(repr(d["title"][:60]))
    return " ".join(bits)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="eventstore")
    p.add_argument("--dir", help="events dir (default: $SUPCOM_EVENTS_DIR or %%LOCALAPPDATA%%\\supcom-events)")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="list events")
    pl.add_argument("--since", type=int, default=0)
    pl.add_argument("--type-prefix")
    pl.add_argument("--source")
    pl.add_argument("--limit", type=int, default=20)
    pl.add_argument("--json", action="store_true")

    pt = sub.add_parser("tail", help="print new events as they arrive")
    pt.add_argument("-n", type=int, default=5, help="show the last N first")
    pt.add_argument("--interval", type=float, default=1.0)
    pt.add_argument("--json", action="store_true")
    pt.add_argument("--once", action="store_true", help="print and exit (for scripts)")

    pa = sub.add_parser("ack", help="set a consumer cursor")
    pa.add_argument("consumer")
    pa.add_argument("seq", type=int)

    sub.add_parser("stats", help="counts and cursors")

    a = p.parse_args(argv)
    store = EventStore(a.dir)
    out = json.dumps if getattr(a, "json", False) else _line

    if a.cmd == "list":
        for e in store.list(a.since, a.type_prefix, a.source, a.limit):
            print(out(e))
    elif a.cmd == "tail":
        last = max(0, store.latest_seq() - a.n)
        try:
            while True:
                for e in store.list(last, limit=200):
                    print(out(e), flush=True)
                    last = e["seq"]
                if a.once:
                    break
                time.sleep(a.interval)
        except KeyboardInterrupt:
            pass
    elif a.cmd == "ack":
        store.set_cursor(a.consumer, a.seq)
        print(f"{a.consumer} cursor = {a.seq}")
    elif a.cmd == "stats":
        print(json.dumps(store.stats(), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
