"""
Database migration runner.

Scans migrations/ for SQL files, applies them in sorted order,
and tracks applied versions in a _migrations table for idempotency.
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


async def run_migrations(db: aiosqlite.Connection) -> None:
    """Apply pending migrations in order. Idempotent."""
    # Ensure migrations tracking table exists
    await db.execute(
        """
        CREATE TABLE IF NOT EXISTS _migrations (
            version     TEXT PRIMARY KEY,
            applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    await db.commit()

    # Gather already-applied versions
    cursor = await db.execute("SELECT version FROM _migrations")
    rows = await cursor.fetchall()
    applied = {row[0] for row in rows}

    # Scan migration files sorted by name
    if not MIGRATIONS_DIR.is_dir():
        logger.warning("[migrate] migrations dir not found: %s", MIGRATIONS_DIR)
        return

    sql_files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    for sql_file in sql_files:
        version = sql_file.stem  # e.g. "001_init"
        if version in applied:
            logger.debug("[migrate] already applied: %s", version)
            continue

        logger.info("[migrate] applying: %s", version)
        sql_text = sql_file.read_text(encoding="utf-8")
        await db.executescript(sql_text)
        await db.execute(
            "INSERT INTO _migrations (version) VALUES (?)", (version,)
        )
        await db.commit()
        logger.info("[migrate] applied: %s", version)
