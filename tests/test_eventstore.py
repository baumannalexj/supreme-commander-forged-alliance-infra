import io
from contextlib import redirect_stdout

from eventstore.__main__ import main as cli
from tests.helpers import TempStoreCase


def ev(i, typ="github.issues.opened", source="a/b"):
    return {"specversion": "1.0", "id": f"id-{i}", "source": source, "type": typ,
            "time": "2026-01-01T00:00:00Z", "subject": f"issues/{i}", "data": {"number": i}}


class Cursors(TempStoreCase):
    def test_append_idempotent_seq_monotonic(self):
        self.assertEqual(self.store.append(ev(1)), (True, 1))
        self.assertEqual(self.store.append(ev(2)), (True, 2))
        self.assertEqual(self.store.append(ev(1)), (False, 1))
        self.assertEqual(self.store.latest_seq(), 2)

    def test_duplicate_does_not_leave_seq_gap(self):
        self.store.append(ev(1))
        self.store.append(ev(1))
        self.assertEqual(self.store.append(ev(2)), (True, 2))

    def test_consumers_are_independent(self):
        for i in range(1, 6):
            self.store.append(ev(i))
        self.assertEqual(self.store.next_events("a", 2)[2], 2)
        b = self.store.next_events("b", 10)
        self.assertEqual((b[0], b[2], len(b[1])), (0, 5, 5))
        before, events, after = self.store.next_events("a", 10)
        self.assertEqual((before, after, [e["seq"] for e in events]), (2, 5, [3, 4, 5]))
        self.assertEqual(self.store.next_events("a", 10), (5, [], 5))

    def test_set_cursor_rewinds(self):
        for i in range(1, 4):
            self.store.append(ev(i))
        self.store.next_events("c", 10)
        self.store.set_cursor("c", 1)
        self.assertEqual([e["seq"] for e in self.store.next_events("c", 10)[1]], [2, 3])

    def test_filters(self):
        self.store.append(ev(1))
        self.store.append(ev(2, "github.push", "x/y"))
        self.assertEqual(len(self.store.list(type_prefix="github.push")), 1)
        self.assertEqual(len(self.store.list(source="a/b")), 1)
        self.assertEqual(len(self.store.list(since_seq=1)), 1)

    def test_bad_consumer_name(self):
        with self.assertRaises(ValueError):
            self.store.cursor("Bad Name")

    def test_wal_mode(self):
        con = self.store._connect()
        try:
            self.assertEqual(con.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        finally:
            con.close()

    def test_cli(self):
        self.store.append(ev(1))
        buf = io.StringIO()
        d = str(self.store.dir)
        with redirect_stdout(buf):
            cli(["--dir", d, "list"])
            cli(["--dir", d, "ack", "me", "1"])
            cli(["--dir", d, "tail", "--once", "-n", "5", "--json"])
            cli(["--dir", d, "stats"])
        out = buf.getvalue()
        self.assertIn("github.issues.opened", out)
        self.assertIn("me cursor = 1", out)
        self.assertEqual(self.store.cursor("me"), 1)
