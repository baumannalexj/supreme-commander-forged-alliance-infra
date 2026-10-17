import json
import urllib.request

from receiver.normalize import normalize
from receiver.server import sign, verify_signature
from tests.helpers import LiveReceiver, TempStoreCase, fixture, validate

CASES = [
    ("issues", "issues_opened.json", "github.issues.opened", "issues/7"),
    ("issue_comment", "issue_comment_created.json", "github.issue_comment.created", "issues/7"),
    ("push", "push.json", "github.push", "refs/heads/main"),
    ("workflow_run", "workflow_run_completed.json", "github.workflow_run.completed", "workflow_run/424242"),
    ("pull_request", "pull_request_opened.json", "github.pull_request.opened", "pull/9"),
]


class Normalise(TempStoreCase):
    def test_fixtures_normalise_and_validate(self):
        for event, name, etype, subject in CASES:
            with self.subTest(name):
                env = normalize(event, "deliv-" + name, fixture(name))
                validate(env, "event.schema.json")
                self.assertEqual(env["type"], etype)
                self.assertEqual(env["subject"], subject)
                self.assertEqual(env["source"], "example-org/example-repo")
                self.assertEqual(env["id"], "deliv-" + name)

    def test_issue_fields(self):
        d = normalize("issues", "x", fixture("issues_opened.json"))["data"]
        self.assertEqual(d["actor"], "octo-user")
        self.assertEqual(d["number"], 7)
        self.assertEqual(d["labels"], ["bug", "area:demo"])
        self.assertEqual(d["title"], "Example issue title")

    def test_comment_body_truncated_and_issue_body_dropped(self):
        payload = fixture("issue_comment_created.json")
        payload["comment"]["body"] = "x" * 9000
        env = normalize("issue_comment", "x", payload)
        validate(env, "event.schema.json")
        self.assertEqual(len(env["data"]["body"]), 4000)
        self.assertTrue(env["data"]["body_truncated"])
        self.assertEqual(env["time"], "2026-01-02T04:00:00Z")

    def test_comment_on_pull_request_subject(self):
        payload = fixture("issue_comment_created.json")
        payload["issue"]["pull_request"] = {"url": "https://example.invalid/pr"}
        self.assertEqual(normalize("issue_comment", "x", payload)["subject"], "pull/7")

    def test_push_and_workflow_data(self):
        d = normalize("push", "x", fixture("push.json"))["data"]
        self.assertEqual((d["ref"], d["commit_count"], d["head_commit_message"]),
                         ("refs/heads/main", 2, "Example commit message"))
        w = normalize("workflow_run", "x", fixture("workflow_run_completed.json"))["data"]
        self.assertEqual((w["conclusion"], w["workflow_name"], w["run_id"]), ("success", "tests", 424242))

    def test_unknown_event_is_generic_but_valid(self):
        env = normalize("star", "x", {"action": "created", "repository": {"full_name": "a/b"}})
        validate(env, "event.schema.json")
        self.assertEqual(env["type"], "github.star.created")


class Signature(LiveReceiver):
    SECRET = "test-secret"

    def test_verify_function(self):
        body = b'{"a":1}'
        self.assertTrue(verify_signature("s", body, sign("s", body)))
        self.assertFalse(verify_signature("s", body, sign("other", body)))
        self.assertFalse(verify_signature("s", body, None))
        self.assertFalse(verify_signature("s", body, "sha1=abc"))

    def test_valid_invalid_missing(self):
        body = json.dumps(fixture("issues_opened.json")).encode()
        code, _ = self.post("issues", body, signature=sign(self.SECRET, body))
        self.assertEqual(code, 202)
        code, _ = self.post("issues", body, delivery="d-2", signature=sign("wrong", body))
        self.assertEqual(code, 401)
        code, _ = self.post("issues", body, delivery="d-3")
        self.assertEqual(code, 401)
        self.assertEqual(self.store.stats()["total"], 1)


class Server(LiveReceiver):
    def test_post_stores_and_is_idempotent(self):
        body = json.dumps(fixture("issues_opened.json")).encode()
        code, r1 = self.post("issues", body, delivery="same")
        code2, r2 = self.post("issues", body, delivery="same")
        self.assertEqual((code, code2), (202, 202))
        self.assertEqual((r1["status"], r2["status"]), ("stored", "duplicate"))
        self.assertEqual(r1["seq"], r2["seq"])
        self.assertEqual(self.store.stats()["total"], 1)
        mirror = self.store.jsonl_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(mirror), 1)
        validate({k: v for k, v in json.loads(mirror[0]).items() if k not in ("seq", "received_at")},
                 "event.schema.json")

    def test_bad_requests(self):
        self.assertEqual(self.post("issues", b"not json")[0], 400)
        self.assertEqual(self.post("", b"{}")[0], 400)
        self.assertEqual(self.post("issues", b"[1]")[0], 400)

    def test_health_and_binding(self):
        with urllib.request.urlopen(f"http://127.0.0.1:{self.port}/health") as r:
            h = json.loads(r.read())
        self.assertEqual(h["status"], "ok")
        self.assertFalse(h["signature_required"])
        self.assertEqual(self.srv.server_address[0], "127.0.0.1")
