"""Static configuration and the hard safety rules of the queue."""
from __future__ import annotations

import os
import re
from pathlib import Path

REPO = os.environ.get("MQ_REPO", "baumannalexj/supreme-commander-forged-alliance")
OWNER = REPO.split("/")[0]            # only this account may queue via /queue
BASE = "performance-optimizations"    # the only branch the queue ever fast-forwards
LABEL = "queue"
WORKFLOW_FILE = "merge-queue-build.yml"
WORKFLOW_NAME = "merge-queue-build"
CONSUMER = "mq"                       # event-store cursor name
MAX_ATTEMPTS = 5                      # rebuilds when the base keeps moving
REMOTE_URL = f"https://github.com/{REPO}.git"

# The ONLY refs the queue may push to or delete. Anything else (main, other branches, tags) is refused.
_ALLOWED_DST = re.compile(rf"^refs/heads/(?:{re.escape(BASE)}|mq/[0-9]+)$")


class UnsafeRef(Exception):
    pass


def assert_pushable(dst: str) -> str:
    """Return ``dst`` if the queue may push to / delete it, else raise. Hard-coded refusal for main etc."""
    if not _ALLOWED_DST.match(dst):
        raise UnsafeRef(f"refusing to push to {dst!r}: the queue may only touch {BASE} and mq/<pr>")
    return dst


def data_dir() -> Path:
    """Git-ignored runtime data (queue db + bare clone). Override with MQ_DATA_DIR."""
    env = os.environ.get("MQ_DATA_DIR")
    d = Path(env) if env else Path(__file__).resolve().parent.parent / "data" / "mq"
    d.mkdir(parents=True, exist_ok=True)
    return d
