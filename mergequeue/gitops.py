"""Git plumbing in a private bare, blobless clone under the data dir. Never touches a developer checkout.

Every push goes through ``_push`` -> ``config.assert_pushable``; there is no force option anywhere.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from . import config


class GitError(Exception):
    pass


class Conflict(Exception):
    pass


class Repo:
    def __init__(self, path: Path):
        self.path = path

    def git(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        p = subprocess.run(["git", "-C", str(self.path), *args], capture_output=True, text=True, encoding="utf-8")
        if check and p.returncode != 0:
            raise GitError(f"git {' '.join(args)}: {(p.stderr or p.stdout).strip()}")
        return p

    def ensure(self) -> None:
        if (self.path / "HEAD").exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        p = subprocess.run(["git", "clone", "--bare", "--filter=blob:none", "--single-branch",
                            "--branch", config.BASE, config.REMOTE_URL, str(self.path)],
                           capture_output=True, text=True, encoding="utf-8")
        if p.returncode != 0:
            raise GitError(f"clone failed: {p.stderr.strip()}")

    def fetch_base(self) -> str:
        self.git("fetch", "-q", "--filter=blob:none", "origin",
                 f"+refs/heads/{config.BASE}:refs/remotes/origin/{config.BASE}")
        return self.git("rev-parse", f"refs/remotes/origin/{config.BASE}").stdout.strip()

    def fetch_pr(self, n: int) -> str:
        self.git("fetch", "-q", "--filter=blob:none", "origin", f"+refs/pull/{n}/head:refs/pr/{n}")
        return self.git("rev-parse", f"refs/pr/{n}").stdout.strip()

    def is_ancestor(self, a: str, b: str) -> bool:
        return self.git("merge-base", "--is-ancestor", a, b, check=False).returncode == 0

    def merge_commit(self, base: str, head: str, pr: int) -> str:
        """Merge commit (no squash) of ``head`` into ``base``, built without a work tree. Raises Conflict."""
        p = self.git("merge-tree", "--write-tree", "--name-only", base, head, check=False)
        if p.returncode == 1:
            files = [ln for ln in p.stdout.splitlines()[1:] if ln.strip() and "CONFLICT" not in ln][:10]
            raise Conflict(", ".join(files) or "conflicting changes")
        if p.returncode != 0:
            raise GitError(f"merge-tree: {(p.stderr or p.stdout).strip()}")
        tree = p.stdout.splitlines()[0].strip()
        msg = f"Merge pull request #{pr} (merge queue)"
        ident = {"GIT_AUTHOR_NAME": config.GIT_NAME, "GIT_AUTHOR_EMAIL": config.GIT_EMAIL,
                 "GIT_COMMITTER_NAME": config.GIT_NAME, "GIT_COMMITTER_EMAIL": config.GIT_EMAIL}
        p = subprocess.run(["git", "-C", str(self.path), "commit-tree", tree, "-p", base, "-p", head, "-m", msg],
                           capture_output=True, text=True, encoding="utf-8", env={**os.environ, **ident})
        if p.returncode != 0:
            raise GitError(f"commit-tree: {p.stderr.strip()}")
        return p.stdout.strip()

    def _push(self, src: str, dst: str) -> subprocess.CompletedProcess:
        config.assert_pushable(dst)
        if src.startswith("+") or "force" in src:
            raise config.UnsafeRef("force pushes are never allowed")
        return self.git("push", "-q", "origin", f"{src}:{dst}", check=False)

    def push_mq(self, sha: str, pr: int) -> None:
        dst = f"refs/heads/mq/{pr}"
        # a rebuilt mq/<pr> is not a descendant of the old one, so drop the old branch first (delete is allowed for mq/*)
        self.delete_mq(pr)
        p = self._push(sha, dst)
        if p.returncode != 0:
            raise GitError(f"push {dst}: {p.stderr.strip()}")

    def delete_mq(self, pr: int) -> None:
        self._push("", f"refs/heads/mq/{pr}")  # `:<dst>` = delete; a missing branch is not an error here

    def fast_forward_base(self, sha: str) -> bool:
        """Fast-forward the base to ``sha``. False if the remote refused (base moved)."""
        remote_base = self.fetch_base()
        if not self.is_ancestor(remote_base, sha):
            return False
        return self._push(sha, f"refs/heads/{config.BASE}").returncode == 0
