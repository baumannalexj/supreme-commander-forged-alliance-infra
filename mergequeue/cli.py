"""``mq`` CLI: status | enqueue | remove | run | poll.

Run it as ``uv run python -m mergequeue <cmd>`` (or ``.\\mq.ps1 <cmd>``).
"""
from __future__ import annotations

import os
import time

import click

from eventstore import EventStore

from . import config, gh
from .engine import Engine
from .gitops import GitError, Repo
from .state import Queue


def make_engine() -> Engine:
    d = config.data_dir()
    return Engine(Queue(d / "mq.sqlite"), Repo(d / "clone.git"))


def _single_instance(path):
    """Hold an OS file lock for the life of the process (released automatically if it dies)."""
    fh = open(path, "a+")
    try:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


@click.group()
def cli() -> None:
    """Simulated merge queue for baumannalexj/supreme-commander-forged-alliance -> performance-optimizations."""


@cli.command()
def status() -> None:
    """Show the queue and the last log lines."""
    eng = make_engine()
    rows = eng.q.all()
    click.echo(f"repo {config.REPO}  base {config.BASE}  data {config.data_dir()}")
    if not rows:
        click.echo("queue: empty")
    for i, e in enumerate(rows, 1):
        run = f" run {e['run_id']} {e['run_url']}" if e["run_id"] else ""
        mq = f" mq/{e['pr']}@{e['mq_sha'][:10]}" if e["mq_sha"] else ""
        click.echo(f"{i}. #{e['pr']} {e['status']} via {e['trigger']} attempts={e['attempts']}{mq}{run}")
    for r in eng.q.recent_log():
        click.echo(f"  {r['ts']} {'#%d ' % r['pr'] if r['pr'] else ''}{r['msg']}")


@cli.command()
@click.argument("pr", type=int)
def enqueue(pr: int) -> None:
    """Queue PR (adds the `queue` label, creating it if missing)."""
    eng = make_engine()
    gh.ensure_label()
    p = gh.pr(pr)
    if p["state"] == "open" and p["base"]["ref"] == config.BASE and not gh.has_label(p):
        gh.api(f"{gh.R}/issues/{pr}/labels", "POST", {"labels[]": config.LABEL})
    click.echo(eng.enqueue(pr, "cli"))


@cli.command()
@click.argument("pr", type=int)
def remove(pr: int) -> None:
    """Take PR out of the queue and drop its `queue` label."""
    eng = make_engine()
    if eng.q.get(pr) is None:
        click.echo(f"#{pr} is not queued")
        return
    eng.remove(pr, "mq remove", note=True)
    click.echo(f"#{pr} removed")


@cli.command()
@click.option("--interval", default=5.0, show_default=True, help="seconds between loop iterations")
@click.option("--check-every", default=30.0, show_default=True, help="seconds between direct run-status checks")
@click.option("--poll-every", default=120.0, show_default=True, help="seconds between full API reconciles (0 = never)")
@click.option("--once", is_flag=True, help="handle pending events + one tick, then exit")
def run(interval: float, check_every: float, poll_every: float, once: bool) -> None:
    """Dispatcher loop. Consumes events from the infra repo's local event store (fed by its webhook receiver)."""
    eng = make_engine()
    lock = _single_instance(config.data_dir() / "run.lock")
    if lock is None:
        raise click.ClickException("another `mq run` already holds data/mq/run.lock")
    store = EventStore()
    if config.CONSUMER not in store.stats()["consumers"]:
        store.set_cursor(config.CONSUMER, store.latest_seq())  # first start: ignore history
    click.echo(f"mq run: events {store.dir}, data {config.data_dir()}")
    last_check = last_poll = 0.0
    if poll_every:
        eng.reconcile()  # catch up on anything missed while we were down
        last_poll = time.monotonic()
    while True:
        cursor = store.cursor(config.CONSUMER)
        for ev in store.list(cursor, source=config.REPO, limit=200):
            try:
                eng.handle_event(ev)
            except (GitError, gh.GhError) as exc:
                eng.q.log(None, f"error handling event {ev['seq']} {ev['type']}: {exc}")
            store.set_cursor(config.CONSUMER, ev["seq"])
        now = time.monotonic()
        due_check = now - last_check >= check_every
        try:
            eng.tick(check_runs=due_check)
            if poll_every and now - last_poll >= poll_every:
                eng.reconcile()
                last_poll = now
        except (GitError, gh.GhError) as exc:
            eng.q.log(None, f"error: {exc}")
        if due_check:
            last_check = now
        if once:
            return
        time.sleep(interval)


@cli.command()
def poll() -> None:
    """Reconcile from the GitHub API once (fallback when a webhook was missed)."""
    make_engine().reconcile()


def main() -> None:
    cli()
