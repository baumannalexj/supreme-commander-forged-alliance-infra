"""POST a synthetic fixture to the local receiver (signed when SUPCOM_WEBHOOK_SECRET is set).

  uv run python tools/send_test_event.py issues_opened.json [--event issues] [--delivery ID]
"""
import argparse
import hashlib
import hmac
import json
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
EVENT_FOR = {"issues": "issues", "issue": "issue_comment", "push": "push",
             "workflow": "workflow_run", "pull": "pull_request"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("fixture", help="file name in tests/fixtures")
    ap.add_argument("--event", help="X-GitHub-Event (default: guessed from the file name)")
    ap.add_argument("--delivery", default=str(uuid.uuid4()))
    ap.add_argument("--port", type=int, default=int(os.environ.get("SUPCOM_RECEIVER_PORT", 47800)))
    a = ap.parse_args()
    body = (FIXTURES / a.fixture).read_bytes()
    event = a.event or EVENT_FOR[a.fixture.removesuffix(".json").split("_")[0]]
    req = urllib.request.Request(f"http://127.0.0.1:{a.port}/webhook", data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-GitHub-Event", event)
    req.add_header("X-GitHub-Delivery", a.delivery)
    secret = os.environ.get("SUPCOM_WEBHOOK_SECRET")
    if secret:
        req.add_header("X-Hub-Signature-256", "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest())
    try:
        with urllib.request.urlopen(req) as r:
            print(r.status, r.read().decode())
    except urllib.error.HTTPError as e:
        print(e.code, e.read().decode())
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
