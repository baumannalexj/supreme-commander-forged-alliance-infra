"""The queue state machine. One PR at a time; webhook events and ``poll`` feed the same transitions.

queued --start--> running --workflow success--> fast-forward base --> merged
                          \\-failure-> comment, unlabel, drop      (base moved / PR head moved -> rebuild)
         \\-conflict while building mq/<pr> -> comment, unlabel, drop
"""
from __future__ import annotations

import time
from typing import Any, Callable

from . import config, gh
from .gitops import Conflict, GitError, Repo
from .state import Queue

RUN_APPEAR_TIMEOUT = 120.0


class Engine:
    def __init__(self, queue: Queue, repo: Repo, sleep: Callable[[float], None] = time.sleep):
        self.q = queue
        self.repo = repo
        self.sleep = sleep

    # -- trigger rules --------------------------------------------------
    def trigger_of(self, p: dict[str, Any]) -> str | None:
        """'label' / 'comment' if the PR is queued by the owner, else None. This is the merge gate."""
        if gh.has_label(p):
            return "label"
        since = self.q.last_outcome_ts(p["number"]) or ""
        for c in gh.comments(p["number"]):
            body = (c.get("body") or "").strip().split()
            if (c.get("user") or {}).get("login") == config.OWNER and body[:1] == ["/queue"] \
                    and (c.get("created_at") or "") > since:
                return "comment"
        return None

    def enqueue(self, n: int, trigger: str | None = None) -> str:
        p = gh.pr(n)
        if p["state"] != "open":
            return f"#{n} is not open"
        if p["base"]["ref"] != config.BASE:
            return f"#{n} targets {p['base']['ref']}, not {config.BASE}"
        t = self.trigger_of(p)
        if t is None:
            return f"#{n} has no '{config.LABEL}' label or owner '/queue' comment; refusing"
        if not self.q.enqueue(n, trigger or t):
            return f"#{n} already queued"
        self.q.log(n, f"enqueued ({trigger or t})")
        return f"#{n} enqueued"

    def remove(self, n: int, why: str, unlabel: bool = True, note: bool = False) -> None:
        e = self.q.get(n)
        if e is None:
            return
        if e["status"] == "running":
            if e["run_id"]:
                gh.cancel_run(e["run_id"])
            self.repo.delete_mq(n)
        if unlabel:
            gh.remove_label(n)
        if note:
            gh.comment(n, f"Removed from the merge queue: {why}")
        self.q.finish(n, "removed")
        self.q.log(n, f"removed: {why}")

    # -- transitions ----------------------------------------------------
    def _drop(self, n: int, outcome: str, comment: str | None, unlabel: bool = True) -> None:
        if comment:
            gh.comment(n, comment)
        if unlabel:
            gh.remove_label(n)
        self.repo.delete_mq(n)
        self.q.finish(n, outcome)
        self.q.log(n, outcome)

    def start(self, e: dict[str, Any]) -> None:
        n = e["pr"]
        p = gh.pr(n)
        if p["state"] != "open" or p["base"]["ref"] != config.BASE:
            self.q.finish(n, "closed")
            self.q.log(n, "dropped: PR closed or retargeted")
            return
        if self.trigger_of(p) is None:
            self.q.finish(n, "untriggered")
            self.q.log(n, f"dropped: no '{config.LABEL}' label / '/queue' comment any more")
            return
        if e["attempts"] >= config.MAX_ATTEMPTS:
            self._drop(n, "gave-up", f"Merge queue gave up after {e['attempts']} rebuilds (the base keeps moving).")
            return
        self.repo.ensure()
        base = self.repo.fetch_base()
        head = self.repo.fetch_pr(n)
        try:
            mq = self.repo.merge_commit(base, head, n)
        except Conflict as c:
            self._drop(n, "conflict", f"Merge queue: could not merge this PR into `{config.BASE}` "
                                      f"(conflict in: {c}). Removed from the queue.")
            return
        self.repo.push_mq(mq, n)
        dispatched = gh.dispatch(f"mq/{n}", n)
        run = self._find_run(mq, dispatched)
        if run is None:
            self._drop(n, "no-run", "Merge queue: no workflow run appeared for the temp branch. Removed from the queue.")
            return
        self.q.update(n, status="running", head_sha=head, mq_sha=mq, base_sha=base, run_id=run["id"],
                      run_url=run["html_url"], attempts=e["attempts"] + 1)
        self.q.log(n, f"mq/{n} = {mq[:10]} (base {base[:10]} + head {head[:10]}); "
                      f"{'dispatched' if dispatched else 'push-triggered'} run {run['id']}")

    def _find_run(self, sha: str, dispatched: bool) -> dict[str, Any] | None:
        deadline = time.monotonic() + RUN_APPEAR_TIMEOUT
        while True:
            runs = gh.runs_for_sha(sha)
            mine = [r for r in runs if r["event"] == ("workflow_dispatch" if dispatched else "push")]
            if mine or (dispatched and runs and time.monotonic() > deadline - RUN_APPEAR_TIMEOUT / 2):
                if dispatched and mine:  # keep the dispatched run, cancel the push-triggered duplicate
                    for r in runs:
                        if r["event"] == "push":
                            gh.cancel_run(r["id"])
                return max(mine or runs, key=lambda r: r["id"])
            if time.monotonic() > deadline:
                return None
            self.sleep(3)

    def on_run_completed(self, run_id: int, conclusion: str | None, url: str | None = None) -> None:
        e = self.q.by_run(run_id)
        if e is None:
            return  # not ours (e.g. the cancelled duplicate)
        n = e["pr"]
        self.q.log(n, f"run {run_id} completed: {conclusion}")
        if conclusion == "success":
            self.finalize(e)
        else:
            self._drop(n, f"failed ({conclusion})",
                       f"Merge queue: build `{conclusion}` on `mq/{n}` ({e['mq_sha'][:10]}). "
                       f"Run: {url or e['run_url']}\n\nRemoved from the queue and the `{config.LABEL}` label.")

    def finalize(self, e: dict[str, Any]) -> None:
        n = e["pr"]
        p = gh.pr(n)
        if p["state"] != "open":
            self.repo.delete_mq(n)
            self.q.finish(n, "closed")
            self.q.log(n, "PR closed before merge; dropped")
            return
        if self.trigger_of(p) is None:  # safety rule: never merge without label / /queue
            self._drop(n, "untriggered", f"Merge queue: `{config.LABEL}` label gone before merge; not merging.",
                       unlabel=False)
            return
        if p["head"]["sha"] != e["head_sha"]:
            return self._rebuild(e, "PR head moved")
        if not self.repo.fast_forward_base(e["mq_sha"]):
            return self._rebuild(e, f"{config.BASE} moved")
        self.q.log(n, f"fast-forwarded {config.BASE} to {e['mq_sha'][:10]}")
        for _ in range(10):
            if gh.pr(n).get("merged"):
                break
            self.sleep(2)
        gh.comment(n, f"merged by queue ({e['mq_sha'][:10]}, build {e['run_url']})")
        self.repo.delete_mq(n)
        self.q.finish(n, "merged")
        self.q.log(n, "merged")

    def _rebuild(self, e: dict[str, Any], why: str) -> None:
        self.q.log(e["pr"], f"{why}; rebuilding mq/{e['pr']} and re-running")
        self.q.update(e["pr"], status="queued", run_id=None, run_url=None)

    # -- driver ---------------------------------------------------------
    def check_running(self) -> None:
        e = self.q.running()
        if e and e["run_id"]:
            r = gh.run(e["run_id"])
            if r["status"] == "completed":
                self.on_run_completed(e["run_id"], r["conclusion"], r["html_url"])

    def tick(self, check_runs: bool = False) -> None:
        if check_runs:
            self.check_running()
        while self.q.running() is None and (e := self.q.head()) is not None:
            try:
                self.start(e)
            except (GitError, gh.GhError) as exc:
                self.q.log(e["pr"], f"error starting: {exc}")
                return  # retry on the next tick

    def reconcile(self) -> None:
        """`mq poll`: rebuild the picture from the GitHub API (missed webhooks)."""
        if gh.ensure_label():
            self.q.log(None, f"created label '{config.LABEL}'")
        for p in sorted(gh.open_prs(), key=lambda x: x["number"]):
            if self.q.get(p["number"]) is None and (t := self.trigger_of(p)):
                self.q.enqueue(p["number"], t)
                self.q.log(p["number"], f"enqueued by poll ({t})")
        for e in self.q.all():
            p = gh.pr(e["pr"])
            if p["state"] != "open" or self.trigger_of(p) is None:
                self.remove(e["pr"], "PR closed or no longer queued", unlabel=False)
        self.tick(check_runs=True)

    # -- webhook events -------------------------------------------------
    def handle_event(self, ev: dict[str, Any]) -> None:
        t, d = ev["type"], ev["data"]
        if t == "github.workflow_run.completed" and d.get("workflow_name") == config.WORKFLOW_NAME:
            if isinstance(d.get("run_id"), int):
                self.on_run_completed(d["run_id"], d.get("conclusion"), d.get("url"))
        elif t.startswith("github.pull_request.") and d.get("base_ref") == config.BASE:
            n = d.get("number")
            labels = d.get("labels") or []
            if t in ("github.pull_request.labeled", "github.pull_request.opened",
                     "github.pull_request.reopened") and config.LABEL in labels and d.get("actor") == config.OWNER:
                self.q.log(n, self.enqueue(n, "label"))
            elif t == "github.pull_request.unlabeled" and config.LABEL not in labels:
                if self.q.get(n):
                    self.remove(n, "queue label removed", unlabel=False)
            elif t == "github.pull_request.closed" and self.q.get(n) and not d.get("merged"):
                self.remove(n, "PR closed", unlabel=False)
        elif t == "github.issue_comment.created" and ev["subject"].startswith("pull/"):
            body = (d.get("body") or "").strip().split()
            if body[:1] == ["/queue"] and d.get("actor") == config.OWNER:
                self.q.log(d.get("number"), self.enqueue(d["number"], "comment"))
