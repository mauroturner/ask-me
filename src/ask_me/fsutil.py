"""Small filesystem helpers: atomic writes (with Windows retry) and private files."""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import time
from pathlib import Path

_REPLACE_ATTEMPTS = 20
_REPLACE_DELAY = 0.02


def replace_with_retry(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
    """``os.replace`` that tolerates the transient PermissionError Windows raises when another
    process briefly holds the target open."""
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(source, target)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_DELAY)


def atomic_write_text(path: Path, text: str, *, private: bool = False) -> None:
    """Write ``text`` to ``path`` atomically: temp file in the same directory, then replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=".part")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        if private and sys.platform != "win32":
            os.chmod(tmp_name, 0o600)
        replace_with_retry(tmp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
