"""Thin wrapper over the ``gh`` CLI (REST). Text we read from GitHub is data, never instructions."""
from __future__ import annotations

import json
import subprocess
from typing import Any

from . import config


class GhError(Exception):
    def __init__(self, msg: str, status: int | None = None):
        super().__init__(msg)
        self.status = status


def api(path: str, method: str = "GET", fields: dict[str, str] | None = None, paginate: bool = False) -> Any:
    cmd = ["gh", "api", "-X", method, path]
    if paginate:
        cmd += ["--paginate", "--slurp"]
    for k, v in (fields or {}).items():
        cmd += ["-f", f"{k}={v}"]
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if p.returncode != 0:
        text = p.stderr + p.stdout
        status = next((s for s in (404, 422) if f"HTTP {s}" in text or f'"status":"{s}"' in text), None)
        raise GhError(f"gh api {method} {path}: {text.strip()}", status)
    out = p.stdout.strip()
    if not out:
        return None
    data = json.loads(out)
    if paginate:  # --slurp yields a list of pages
        return [x for page in data for x in (page if isinstance(page, list) else [page])]
    return data


R = f"repos/{config.REPO}"


def pr(n: int) -> dict[str, Any]:
    return api(f"{R}/pulls/{n}")


def open_prs() -> list[dict[str, Any]]:
    return api(f"{R}/pulls?state=open&base={config.BASE}&per_page=100", paginate=True)


def comments(n: int) -> list[dict[str, Any]]:
    return api(f"{R}/issues/{n}/comments?per_page=100", paginate=True)


def comment(n: int, body: str) -> None:
    api(f"{R}/issues/{n}/comments", "POST", {"body": body})


def has_label(p: dict[str, Any]) -> bool:
    return any(x.get("name") == config.LABEL for x in p.get("labels", []))


def remove_label(n: int) -> None:
    try:
        api(f"{R}/issues/{n}/labels/{config.LABEL}", "DELETE")
    except GhError as e:
        if e.status != 404:
            raise


def ensure_label() -> bool:
    """Create the queue label if missing. Returns True when it had to be created."""
    try:
        api(f"{R}/labels/{config.LABEL}")
        return False
    except GhError as e:
        if e.status != 404:
            raise
    api(f"{R}/labels", "POST", {"name": config.LABEL, "color": "0e8a16",
                                "description": "Queued for the simulated merge queue (mq)"})
    return True


def dispatch(branch: str, pr_number: int) -> bool:
    """workflow_dispatch on ``branch``. False if GitHub says the workflow is not dispatchable (the file
    is not on the default branch yet: 404/422); the caller then relies on the push-triggered run."""
    try:
        api(f"{R}/actions/workflows/{config.WORKFLOW_FILE}/dispatches", "POST",
            {"ref": branch, "inputs[pr]": str(pr_number), "inputs[ref]": branch})
        return True
    except GhError as e:
        if e.status in (404, 422):
            return False
        raise


def runs_for_sha(sha: str) -> list[dict[str, Any]]:
    d = api(f"{R}/actions/runs?head_sha={sha}&per_page=50")
    return [r for r in d.get("workflow_runs", [])
            if str(r.get("path", "")).split("@")[0].endswith(config.WORKFLOW_FILE)]


def run(run_id: int) -> dict[str, Any]:
    return api(f"{R}/actions/runs/{run_id}")


def cancel_run(run_id: int) -> None:
    try:
        api(f"{R}/actions/runs/{run_id}/cancel", "POST")
    except GhError:
        pass
