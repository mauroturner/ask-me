"""Shared on-disk state so several processes can wait on one bot (S5).

Layout under ``<configDir>``::

    pending/<nonce>.json   one file per unanswered question
    inbox/<update_id>.json every update from the configured chat, written by whoever polled it
    state/offset           last confirmed ``getUpdates`` offset

Every write is atomic (temp file + ``os.replace``). An answer is claimed with ``os.rename`` to a
unique name: exactly one process wins, the others see ``FileNotFoundError``.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path

from .channel import Incoming
from .clock import Clock, SystemClock
from .fsutil import atomic_write_text

MAX_AGE_SECONDS = 24 * 3600


@dataclass(frozen=True)
class Pending:
    nonce: str
    message_id: int
    created_at: float
    expires_at: float
    allow_free_text: bool


@dataclass(frozen=True)
class InboxEntry:
    update_id: int
    path: Path
    incoming: Incoming


class Inbox:
    def __init__(self, config_dir: Path, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()
        self.pending_dir = config_dir / "pending"
        self.inbox_dir = config_dir / "inbox"
        self.offset_path = config_dir / "state" / "offset"

    # ---- pending registry ---------------------------------------------------------------
    def register(self, pending: Pending) -> None:
        atomic_write_text(self.pending_dir / f"{pending.nonce}.json", json.dumps(asdict(pending)))

    def unregister(self, nonce: str) -> None:
        with contextlib.suppress(OSError):
            (self.pending_dir / f"{nonce}.json").unlink()

    def pending(self) -> list[Pending]:
        """Live (unexpired) pending questions from every process."""
        now = self._clock.now()
        found: list[Pending] = []
        for path in self._json_files(self.pending_dir):
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                pending = Pending(**raw)
            except (OSError, ValueError, TypeError):
                continue
            if pending.expires_at >= now:
                found.append(pending)
        return found

    # ---- offset -------------------------------------------------------------------------
    def read_offset(self) -> int | None:
        try:
            return int(self.offset_path.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return None

    def _advance_offset(self, value: int) -> None:
        current = self.read_offset()
        if current is None or value > current:
            atomic_write_text(self.offset_path, f"{value}\n")

    def confirm_through(self, last_update_id: int) -> None:
        """Record that updates up to and including ``last_update_id`` are confirmed."""
        self._advance_offset(last_update_id + 1)

    # ---- inbox ------------------------------------------------------------------------
    def store(self, updates: list[Incoming], last_update_id: int | None) -> None:
        """Persist updates from the configured chat, then confirm everything polled (foreign ids too)."""
        offset = self.read_offset()
        for incoming in updates:
            if offset is not None and incoming.update_id < offset:
                continue  # already confirmed: a redelivery from a poller with a stale offset
            record = {"stored_at": self._clock.now(), "incoming": incoming.to_dict()}
            atomic_write_text(self.inbox_dir / f"{incoming.update_id}.json", json.dumps(record))
        if last_update_id is not None:
            self.confirm_through(last_update_id)

    def entries(self) -> list[InboxEntry]:
        found: list[InboxEntry] = []
        for path in self._json_files(self.inbox_dir):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
                incoming = Incoming.from_dict(record["incoming"])
            except FileNotFoundError:
                continue  # claimed between listing and reading
            except PermissionError:
                continue  # Windows: file is being claimed right now
            except (OSError, ValueError, KeyError, TypeError):
                with contextlib.suppress(OSError):
                    path.unlink()  # writes are atomic, so a half-written file means corruption
                continue
            found.append(InboxEntry(incoming.update_id, path, incoming))
        found.sort(key=lambda entry: entry.update_id)
        return found

    def claim(self, entry: InboxEntry) -> bool:
        """Atomically take ownership of an inbox entry. True for exactly one caller."""
        claimed = entry.path.with_name(f"{entry.path.name}.claimed-{uuid.uuid4().hex}")
        try:
            os.rename(entry.path, claimed)
        except FileNotFoundError:
            return False  # another process claimed it
        except PermissionError:
            return False  # Windows: someone has it open; the caller retries on its next scan
        with contextlib.suppress(OSError):
            claimed.unlink()
        return True

    # ---- housekeeping -------------------------------------------------------------------
    def prune(self) -> None:
        """Drop inbox entries older than 24 h, expired pending files and leftover claimed files."""
        now = self._clock.now()
        for path in self._json_files(self.inbox_dir):
            try:
                stored_at = float(json.loads(path.read_text(encoding="utf-8"))["stored_at"])
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if now - stored_at > MAX_AGE_SECONDS:
                with contextlib.suppress(OSError):
                    path.unlink()
        for path in self._json_files(self.pending_dir):
            try:
                expires_at = float(json.loads(path.read_text(encoding="utf-8"))["expires_at"])
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if expires_at < now:
                with contextlib.suppress(OSError):
                    path.unlink()
        real_now = time.time()
        if self.inbox_dir.is_dir():
            for path in self.inbox_dir.glob("*.claimed-*"):
                with contextlib.suppress(OSError):
                    if real_now - path.stat().st_mtime > MAX_AGE_SECONDS:
                        path.unlink()

    @staticmethod
    def _json_files(folder: Path) -> list[Path]:
        if not folder.is_dir():
            return []
        return sorted(folder.glob("*.json"))


