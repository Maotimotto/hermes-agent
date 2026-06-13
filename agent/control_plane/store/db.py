"""
SQLite connection management for the Session Store.

Provides async aiosqlite connections with WAL mode, foreign keys,
and busy_timeout. Also houses the migration runner.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import aiosqlite

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path.home() / ".hermes" / "control_plane.db"


async def create_database(
    db_path: str | Path | None = None,
) -> aiosqlite.Connection:
    """Create and configure an async SQLite connection.

    Sets WAL journal mode, foreign_keys=ON, busy_timeout=5000.
    """
    path = Path(db_path) if db_path else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)

    db = await aiosqlite.connect(str(path))
    await db.execute("PRAGMA journal_mode = WAL")
    await db.execute("PRAGMA foreign_keys = ON")
    await db.execute("PRAGMA busy_timeout = 5000")
    # Return rows as dict-like Row objects
    db.row_factory = aiosqlite.Row
    return db
