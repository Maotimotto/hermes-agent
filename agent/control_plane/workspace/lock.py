"""Workspace locking via file-based advisory locks (fcntl.flock).

Concurrent-safety guarantee:
    The same workspace can only be held by one session at a time.
    `acquire()` uses exclusive file locking (LOCK_EX | LOCK_NB) so
    that a second attempt from a different session returns False
    immediately without blocking.

Lock files live at:  ~/.hermes/workspaces/.locks/{workspace_id}.lock
"""

from __future__ import annotations

import asyncio
import fcntl
import json
import time
from pathlib import Path

# Default lock directory — under the hermes workspaces root
_LOCKS_DIR = Path.home() / ".hermes" / "workspaces" / ".locks"


class WorkspaceLock:
    """File-based advisory lock for a single workspace.

    Usage::

        lock = WorkspaceLock(workspace_id)
        if await lock.acquire(session_id, ttl=3600):
            # got the lock
            ...
            await lock.release(session_id)
    """

    def __init__(self, workspace_id: str, locks_dir: Path | None = None) -> None:
        self.workspace_id = workspace_id
        self._locks_dir = locks_dir or _LOCKS_DIR
        self._lock_path = self._locks_dir / f"{workspace_id}.lock"
        self._fd: int | None = None

    async def acquire(self, session_id: str, ttl: int = 3600) -> bool:
        """Try to acquire the lock for *session_id* with *ttl* seconds.

        Returns True on success, False if already held by another session.

        The lock metadata (session_id, expires_at) is written into the
        lock file so that stale locks can be detected.
        """
        return await asyncio.to_thread(self._acquire_sync, session_id, ttl)

    async def release(self, session_id: str) -> bool:
        """Release the lock, but only if held by *session_id*.

        Returns True if released, False if not held by this session.
        """
        return await asyncio.to_thread(self._release_sync, session_id)

    # ── synchronous internals (run in thread) ────────────────────────

    def _acquire_sync(self, session_id: str, ttl: int) -> bool:
        self._locks_dir.mkdir(parents=True, exist_ok=True)
        # Ensure the lock file exists so we can open it
        self._lock_path.touch(exist_ok=True)

        fd = self._lock_path.open("r+")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (IOError, OSError):
            # Lock is held by another process
            fd.close()
            # Check for stale lock (expired TTL)
            if self._is_stale():
                # Force-remove stale lock and retry once
                self._break_stale_lock()
                return self._acquire_sync(session_id, ttl)
            return False

        # We got the exclusive lock — write metadata
        metadata = {
            "session_id": session_id,
            "acquired_at": time.time(),
            "expires_at": time.time() + ttl,
        }
        fd.seek(0)
        fd.truncate()
        fd.write(json.dumps(metadata))
        fd.flush()
        # Keep fd open; flock stays held while fd is open
        self._fd = fd
        return True

    def _release_sync(self, session_id: str) -> bool:
        if self._fd is None:
            return False

        # Verify we are the holder
        try:
            self._lock_path.seek(0) if hasattr(self._lock_path, 'seek') else None
            # Re-read metadata from file
            self._fd.seek(0)
            data = json.loads(self._fd.read())
            if data.get("session_id") != session_id:
                return False
        except (json.JSONDecodeError, OSError):
            pass  # corrupted lock file — release anyway

        try:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            self._fd.close()
            self._fd = None
            return True
        except (IOError, OSError):
            return False

    def _is_stale(self) -> bool:
        """Check if the lock file contains expired metadata."""
        try:
            data = json.loads(self._lock_path.read_text())
            return data.get("expires_at", float("inf")) < time.time()
        except (json.JSONDecodeError, OSError):
            return False

    def _break_stale_lock(self) -> None:
        """Remove a stale lock file."""
        try:
            self._lock_path.unlink(missing_ok=True)
        except OSError:
            pass
