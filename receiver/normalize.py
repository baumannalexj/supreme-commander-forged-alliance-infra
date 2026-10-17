"""Normalise a GitHub webhook delivery into the event envelope (contracts/event.schema.json).

The output keeps only a small, trimmed subset of the payload. Free text is truncated;
it is untrusted content and consumers must never treat it as instructions.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any

BODY_MAX = 4000
TITLE_MAX = 300
MSG_MAX = 500
SPECVERSION = "1.0"


def _get(obj: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(obj, dict):
            return None
        obj = obj.get(key)
    return obj


def _trunc(text: Any, limit: int) -> str | None:
    if not isinstance(text, str):
        return None
    return text if len(text) <= limit else text[:limit]


def _put(data: dict, key: str, value: Any) -> None:
    if value is not None:
        data[key] = value


def _body(data: dict, text: Any) -> None:
    if isinstance(text, str):
        data["body"] = text[:BODY_MAX]
        if len(text) > BODY_MAX:
            data["body_truncated"] = True


def _labels(obj: Any) -> list[str] | None:
    labels = _get(obj, "labels")
    if not isinstance(labels, list):
        return None
    return [str(x["name"])[:100] for x in labels if isinstance(x, dict) and "name" in x][:50]


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def normalize(event: str, delivery_id: str | None, payload: dict[str, Any],
              raw_body: bytes = b"") -> dict[str, Any]:
    """Return the envelope for one delivery. ``event`` is the X-GitHub-Event header."""
    action = payload.get("action") if isinstance(payload.get("action"), str) else None
    source = _get(payload, "repository", "full_name") or "unknown/unknown"
    data: dict[str, Any] = {}
    _put(data, "actor", _get(payload, "sender", "login"))
    _put(data, "action", action)
    subject = "repo"
    when: Any = None

    if event == "issues":
        issue = payload.get("issue") or {}
        _issue_fields(data, issue)
        _put(data, "label", _get(payload, "label", "name"))
        subject = f"issues/{issue.get('number')}"
        when = issue.get("updated_at") or issue.get("created_at")
    elif event == "issue_comment":
        issue = payload.get("issue") or {}
        comment = payload.get("comment") or {}
        _issue_fields(data, issue, with_body=False)
        _body(data, comment.get("body"))
        _put(data, "url", comment.get("html_url") or issue.get("html_url"))
        is_pr = isinstance(issue.get("pull_request"), dict)
        subject = f"{'pull' if is_pr else 'issues'}/{issue.get('number')}"
        when = comment.get("created_at")
    elif event == "pull_request":
        pr = payload.get("pull_request") or {}
        _issue_fields(data, pr)
        _put(data, "draft", pr.get("draft") if isinstance(pr.get("draft"), bool) else None)
        _put(data, "merged", pr.get("merged") if isinstance(pr.get("merged"), bool) else None)
        _put(data, "ref", _get(pr, "head", "ref"))
        _put(data, "base_ref", _get(pr, "base", "ref"))
        subject = f"pull/{pr.get('number')}"
        when = pr.get("updated_at") or pr.get("created_at")
    elif event == "push":
        _put(data, "ref", payload.get("ref"))
        _put(data, "before", payload.get("before"))
        _put(data, "after", payload.get("after"))
        _put(data, "forced", payload.get("forced") if isinstance(payload.get("forced"), bool) else None)
        commits = payload.get("commits")
        if isinstance(commits, list):
            data["commit_count"] = len(commits)
        _put(data, "head_commit_message", _trunc(_get(payload, "head_commit", "message"), MSG_MAX))
        _put(data, "url", payload.get("compare"))
        subject = str(payload.get("ref") or "repo")
        when = _get(payload, "head_commit", "timestamp")
    elif event == "workflow_run":
        run = payload.get("workflow_run") or {}
        _put(data, "workflow_name", _trunc(run.get("name") or _get(payload, "workflow", "name"), TITLE_MAX))
        _put(data, "run_id", run.get("id") if isinstance(run.get("id"), int) else None)
        _put(data, "status", run.get("status"))
        _put(data, "conclusion", run.get("conclusion"))
        _put(data, "ref", run.get("head_branch"))
        _put(data, "title", _trunc(run.get("display_title"), TITLE_MAX))
        _put(data, "url", run.get("html_url"))
        subject = f"workflow_run/{run.get('id')}"
        when = run.get("updated_at") or run.get("created_at")

    etype = f"github.{event}" + (f".{action}" if action else "")
    if not isinstance(when, str):
        when = _now()
    if not delivery_id:
        delivery_id = "body-" + hashlib.sha256(raw_body or repr(sorted(payload)).encode()).hexdigest()[:32]
    return {
        "specversion": SPECVERSION,
        "id": delivery_id,
        "source": source,
        "type": etype,
        "time": when,
        "subject": subject,
        "data": data,
    }


def _issue_fields(data: dict, obj: dict, with_body: bool = True) -> None:
    n = obj.get("number")
    _put(data, "number", n if isinstance(n, int) else None)
    _put(data, "title", _trunc(obj.get("title"), TITLE_MAX))
    _put(data, "url", obj.get("html_url"))
    _put(data, "state", obj.get("state"))
    _put(data, "labels", _labels(obj))
    if with_body:
        _body(data, obj.get("body"))
