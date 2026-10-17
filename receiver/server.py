"""Localhost-only webhook receiver. Stdlib http.server; binds 127.0.0.1 and nothing else."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from eventstore import EventStore

from .normalize import normalize

HOST = "127.0.0.1"
DEFAULT_PORT = 47800
MAX_BODY = 5 * 1024 * 1024


def verify_signature(secret: str, body: bytes, header: str | None) -> bool:
    """Check an ``X-Hub-Signature-256`` header (``sha256=<hex>``) in constant time."""
    if not header or not header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header.strip())


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def make_handler(store: EventStore, secret: str | None):
    class Handler(BaseHTTPRequestHandler):
        server_version = "supcom-receiver/0.1"

        def log_message(self, fmt, *args):  # one line per request on stderr, no bodies
            sys.stderr.write("receiver: " + (fmt % args) + "\n")

        def _reply(self, code: int, obj: dict[str, Any]) -> None:
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/health":
                self._reply(200, {"status": "ok", "signature_required": bool(secret),
                                  "latest_seq": store.latest_seq()})
            else:
                self._reply(404, {"error": "not found"})

        def do_POST(self):
            if self.path != "/webhook":
                self._reply(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                self._reply(413, {"error": "bad or too large Content-Length"})
                return
            body = self.rfile.read(length)
            if secret and not verify_signature(secret, body, self.headers.get("X-Hub-Signature-256")):
                self._reply(401, {"error": "invalid or missing signature"})
                return
            event = self.headers.get("X-GitHub-Event")
            if not event:
                self._reply(400, {"error": "missing X-GitHub-Event"})
                return
            try:
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise ValueError("payload must be a JSON object")
            except ValueError as exc:
                self._reply(400, {"error": f"invalid JSON: {exc}"})
                return
            envelope = normalize(event, self.headers.get("X-GitHub-Delivery"), payload, body)
            inserted, seq = store.append(envelope)
            self._reply(202, {"status": "stored" if inserted else "duplicate",
                              "id": envelope["id"], "seq": seq, "type": envelope["type"]})

    return Handler


def make_server(store: EventStore, port: int = DEFAULT_PORT, secret: str | None = None) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((HOST, port), make_handler(store, secret))


def main() -> int:
    port = int(os.environ.get("SUPCOM_RECEIVER_PORT", DEFAULT_PORT))
    secret = os.environ.get("SUPCOM_WEBHOOK_SECRET") or None
    store = EventStore()
    srv = make_server(store, port, secret)
    sys.stderr.write(f"receiver: listening on http://{HOST}:{port}/webhook "
                     f"(signature {'required' if secret else 'NOT required'}), store {store.dir}\n")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0
