import asyncio
import json
import os
import sys

from jsonschema import ValidationError

from mcp_server import tools
from receiver.normalize import normalize
from tests.helpers import ROOT, TempStoreCase, fixture, validate

TOOLS = ["list_events", "get_event", "next_events", "ack", "stats"]


class Handlers(TempStoreCase):
    def setUp(self):
        super().setUp()
        for event, name, did in [("issues", "issues_opened.json", "1"), ("push", "push.json", "2"),
                                 ("workflow_run", "workflow_run_completed.json", "3")]:
            self.store.append(normalize(event, did, fixture(name)))
        big = fixture("issue_comment_created.json")
        big["comment"]["body"] = "y" * 4000
        self.store.append(normalize("issue_comment", "4", big))

    def check(self, tool, args, result):
        validate(args, f"mcp/{tool}.input.schema.json")
        validate(result, f"mcp/{tool}.output.schema.json")
        return result

    def test_list_events(self):
        args = {"since_seq": 0, "limit": 50}
        r = self.check("list_events", args, tools.list_events(self.store, **args))
        self.assertEqual(r["count"], 4)
        self.assertEqual(r["last_seq"], 4)
        self.assertEqual(len(r["events"][3]["data"]["body"]), tools.COMPACT_TEXT)
        args = {"type_prefix": "github.push"}
        r = self.check("list_events", args, tools.list_events(self.store, **args))
        self.assertEqual([e["id"] for e in r["events"]], ["2"])
        args = {"source": "nope/nope"}
        self.assertEqual(self.check("list_events", args, tools.list_events(self.store, **args))["count"], 0)

    def test_limit_bounds(self):
        for bad in (0, 51):
            with self.assertRaises(ValueError):
                tools.list_events(self.store, limit=bad)
            self.assertRaises(ValidationError, validate, {"limit": bad}, "mcp/list_events.input.schema.json")
        with self.assertRaises(ValueError):
            tools.next_events(self.store, "c", 21)

    def test_get_event_full_text(self):
        r = self.check("get_event", {"id": "4"}, tools.get_event(self.store, "4"))
        self.assertEqual(len(r["event"]["data"]["body"]), 4000)
        r = self.check("get_event", {"id": "zzz"}, tools.get_event(self.store, "zzz"))
        self.assertEqual((r["found"], r["event"]), (False, None))

    def test_next_events_and_ack(self):
        r = self.check("next_events", {"consumer": "w1", "limit": 3}, tools.next_events(self.store, "w1", 3))
        self.assertEqual((r["cursor_before"], r["cursor_after"], r["count"]), (0, 3, 3))
        r = self.check("next_events", {"consumer": "w1"}, tools.next_events(self.store, "w1"))
        self.assertEqual((r["cursor_before"], r["cursor_after"], r["count"]), (3, 4, 1))
        r = self.check("ack", {"consumer": "w1", "seq": 0}, tools.ack(self.store, "w1", 0))
        self.assertEqual(r["last_seq"], 0)
        self.assertEqual(tools.next_events(self.store, "w1")["count"], 4)
        with self.assertRaises(ValueError):
            tools.ack(self.store, "w1", 99)
        with self.assertRaises(ValueError):
            tools.next_events(self.store, "Bad Name")

    def test_stats(self):
        tools.next_events(self.store, "w2", 2)
        r = self.check("stats", {}, tools.stats(self.store))
        self.assertEqual((r["total"], r["latest_seq"], r["consumers"]), (4, 4, {"w2": 2}))
        self.assertEqual(r["by_type"]["github.push"], 1)


class Stdio(TempStoreCase):
    """Start the real server over stdio with the SDK client."""

    def test_server_lists_tools_and_answers_list_events(self):
        self.store.append(normalize("issues", "1", fixture("issues_opened.json")))
        asyncio.run(self._run())

    async def _run(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client
        env = dict(os.environ, SUPCOM_EVENTS_DIR=str(self.store.dir))
        params = StdioServerParameters(command=sys.executable, args=["-m", "mcp_server"], cwd=str(ROOT), env=env)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as session:
                await session.initialize()
                listed = await session.list_tools()
                self.assertEqual(sorted(t.name for t in listed.tools), sorted(TOOLS))
                for t in listed.tools:
                    if t.name in ("list_events", "get_event", "next_events"):
                        self.assertIn("untrusted", (t.description or "").lower())
                res = await session.call_tool("list_events", {"limit": 5})
                self.assertFalse(res.isError)
                payload = res.structuredContent or json.loads(res.content[0].text)
                validate(payload, "mcp/list_events.output.schema.json")
                self.assertEqual(payload["count"], 1)
                bad = await session.call_tool("list_events", {"limit": 500})
                self.assertTrue(bad.isError)
