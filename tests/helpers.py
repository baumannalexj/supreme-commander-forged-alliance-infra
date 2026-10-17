import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
CONTRACTS = ROOT / "contracts"


def _registry() -> Registry:
    reg = Registry()
    for p in CONTRACTS.rglob("*.schema.json"):
        doc = json.loads(p.read_text(encoding="utf-8"))
        reg = reg.with_resource(doc["$id"], Resource.from_contents(doc))
    return reg


REGISTRY = _registry()


def validate(instance, rel_path: str) -> None:
    """Validate against contracts/<rel_path>; raises jsonschema.ValidationError."""
    schema = json.loads((CONTRACTS / rel_path).read_text(encoding="utf-8"))
    Draft202012Validator(schema, registry=REGISTRY).validate(instance)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TempStoreCase(unittest.TestCase):
    def setUp(self):
        from eventstore import EventStore
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)
        self.store = EventStore(self._tmp.name)


class LiveReceiver(TempStoreCase):
    SECRET = None

    def setUp(self):
        super().setUp()
        from receiver.server import make_server
        self.srv = make_server(self.store, port=0, secret=self.SECRET)
        self.port = self.srv.server_address[1]
        t = threading.Thread(target=self.srv.serve_forever, daemon=True)
        t.start()
        self.addCleanup(lambda: (self.srv.shutdown(), self.srv.server_close()))

    def post(self, event: str, body: bytes, delivery: str | None = "d-1", signature: str | None = None):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/webhook", data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if event:
            req.add_header("X-GitHub-Event", event)
        if delivery:
            req.add_header("X-GitHub-Delivery", delivery)
        if signature:
            req.add_header("X-Hub-Signature-256", signature)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            try:
                return e.code, json.loads(e.read())
            finally:
                e.close()
