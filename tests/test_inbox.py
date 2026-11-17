"""Shared inbox claim semantics (S5): exactly one caller may claim an entry."""

from __future__ import annotations

import multiprocessing
import threading
from pathlib import Path

from fakes import FakeClock

from ask_me.channel import Incoming
from ask_me.inbox import Inbox


def _store_one(config_dir: Path, clock: FakeClock, update_id: int = 5) -> None:
    Inbox(config_dir, clock).store(
        [Incoming(update_id=update_id, chat_id="1", kind="message", text="x")], update_id
    )


def test_claim_is_exclusive_across_threads(config_dir: Path, clock: FakeClock) -> None:
    for _ in range(30):
        _store_one(config_dir, clock, update_id=5)
        inbox = Inbox(config_dir, clock)
        (entry,) = inbox.entries()
        wins: list[bool] = []
        barrier = threading.Barrier(16)

        def contend() -> None:
            barrier.wait()
            wins.append(Inbox(config_dir, clock).claim(entry))

        threads = [threading.Thread(target=contend) for _ in range(16)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert sum(wins) == 1
        assert inbox.entries() == []
        # reset the lock so the next round can reuse update_id 5
        (config_dir / "inbox" / "5.json.lock").unlink()
        (config_dir / "state" / "offset").unlink()


def _claim_in_process(config_dir: str, path: str, start, results) -> None:  # pragma: no cover - child
    from ask_me.channel import Incoming as _Incoming
    from ask_me.inbox import Inbox as _Inbox
    from ask_me.inbox import InboxEntry

    entry = InboxEntry(5, Path(path), _Incoming(update_id=5, chat_id="1", kind="message", text="x"))
    start.wait()
    results.put(_Inbox(Path(config_dir)).claim(entry))


def test_claim_is_exclusive_across_processes(config_dir: Path, clock: FakeClock) -> None:
    _store_one(config_dir, clock)
    (entry,) = Inbox(config_dir, clock).entries()
    ctx = multiprocessing.get_context("spawn")
    start = ctx.Event()
    results = ctx.Queue()
    procs = [
        ctx.Process(target=_claim_in_process, args=(str(config_dir), str(entry.path), start, results))
        for _ in range(4)
    ]
    for proc in procs:
        proc.start()
    start.set()
    for proc in procs:
        proc.join(timeout=30)
    outcomes = [results.get(timeout=5) for _ in procs]
    assert sum(outcomes) == 1


def test_redelivered_update_cannot_be_claimed_twice(config_dir: Path, clock: FakeClock) -> None:
    _store_one(config_dir, clock)
    inbox = Inbox(config_dir, clock)
    (entry,) = inbox.entries()
    assert inbox.claim(entry) is True

    # A poller with a stale offset writes the same update again.
    (config_dir / "state" / "offset").unlink()
    _store_one(config_dir, clock)

    assert inbox.entries() == []
    assert inbox.claim(entry) is False
