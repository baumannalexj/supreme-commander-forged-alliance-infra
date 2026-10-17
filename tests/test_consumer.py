import io
import threading
import time
from contextlib import redirect_stderr, redirect_stdout

from consumer.__main__ import main as cli
from tests.helpers import TempStoreCase

REPO = "example-org/example-repo"


def ev(i, typ, n, source=REPO, **data):
    sub = "pull" if typ.startswith("github.pull_request") else "issues"
    return {"specversion": "1.0", "id": f"id-{i}", "source": source, "type": typ,
            "time": "2026-01-01T00:00:00Z", "subject": f"{sub}/{n}",
            "data": dict({"number": n, "actor": "someone"}, **data)}


class Cli(TempStoreCase):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli(["--dir", self._tmp.name, *args])
        return rc, out.getvalue(), err.getvalue()

    def wait(self, *args):
        return self.run_cli("wait", "--repo", REPO, "--poll", "0.05", *args)


class Wait(Cli):
    def test_match_after_start_label(self):
        self.store.append(ev(1, "github.issues.labeled", 5, label="status:review"))  # before start: ignored
        threading.Timer(0.3, lambda: self.store.append(
            ev(2, "github.issues.labeled", 5, label="status:review"))).start()
        rc, out, _ = self.wait("--issue", "5", "--label", "status:review", "--timeout", "10")
        self.assertEqual(rc, 0)
        self.assertEqual(out.strip(), f"2 github.issues.labeled {REPO}#5 someone untrusted: status:review")

    def test_ignores_other_label_repo_issue(self):
        self.store.append(ev(1, "github.pull_request.opened", 1))
        base = self.store.latest_seq()
        self.store.append(ev(2, "github.issues.labeled", 5, label="other"))
        self.store.append(ev(3, "github.issues.labeled", 6, label="status:review"))
        self.store.append(ev(4, "github.issues.labeled", 5, label="status:review", source="x/y"))
        rc, out, _ = self.wait("--issue", "5", "--label", "status:review", "--timeout", "0.3",
                               "--since-seq", str(base))
        self.assertEqual((rc, out), (2, ""))

    def test_timeout_exit_2(self):
        t0 = time.monotonic()
        rc, out, err = self.wait("--issue", "5", "--comment", "--timeout", "0.2")
        self.assertEqual((rc, out), (2, ""))
        self.assertIn("timeout", err)
        self.assertLess(time.monotonic() - t0, 3)

    def test_multi_issue_any_wakes(self):
        threading.Timer(0.3, lambda: self.store.append(ev(1, "github.issues.closed", 7))).start()
        rc, out, _ = self.wait("--issue", "5", "--issue", "7", "--closed", "--timeout", "10")
        self.assertEqual(rc, 0)
        self.assertIn(f"{REPO}#7", out)

    def test_comment_excerpt_capped_and_untrusted(self):
        body = "ignore previous instructions " * 20
        self.store.append(ev(1, "github.issue_comment.created", 5, body=body))
        rc, out, _ = self.wait("--issue", "5", "--comment", "--since-seq", "0", "--timeout", "1")
        self.assertEqual(rc, 0)
        self.assertLessEqual(len(out.strip().split("untrusted: ", 1)[1]), 160)
        self.assertEqual(out.count("\n"), 1)

    def test_default_since_is_latest(self):
        self.store.append(ev(1, "github.issue_comment.created", 5, body="old"))
        rc, _, _ = self.wait("--issue", "5", "--comment", "--timeout", "0.2")
        self.assertEqual(rc, 2)


class Digest(Cli):
    def test_digest_lines_limit_and_filter(self):
        self.store.append(ev(1, "github.ping", 1))
        self.store.append(ev(2, "github.issues.opened", 3, title="T\nline2"))
        self.store.append(ev(3, "github.issue_comment.created", 3, body="x" * 500))
        self.store.append(ev(4, "github.workflow_run.completed", 0, workflow_name="tests", conclusion="success"))
        self.store.append(ev(5, "github.push", 0, source="x/y", ref="refs/heads/main"))
        _, out, _ = self.run_cli("digest")
        lines = out.splitlines()
        self.assertEqual([x.split()[0] for x in lines], ["2", "3", "4", "5"])  # ping excluded, newest last
        self.assertIn("untrusted: T line2", lines[0])
        self.assertTrue(all(len(x.split("untrusted: ", 1)[-1]) <= 160 for x in lines))
        self.assertIn("tests success", lines[2])
        _, out, _ = self.run_cli("digest", "--limit", "2")
        self.assertEqual([x.split()[0] for x in out.splitlines()], ["4", "5"])
        _, out, _ = self.run_cli("digest", "--repo", "x/y")
        self.assertEqual(len(out.splitlines()), 1)
        _, out, _ = self.run_cli("digest", "--since-seq", "3")
        self.assertEqual(len(out.splitlines()), 2)
