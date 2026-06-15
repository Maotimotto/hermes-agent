"""
Database migration runner.

Driver-aware: picks `migrations/sqlite/` or `migrations/mysql/` based on
the live driver's `kind`. Tracks applied versions in `_migrations` for
idempotency. Falls back to the flat `migrations/` directory (legacy
SQLite-only path) when a backend-specific dir is missing.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from .driver import StoreDriver

logger = logging.getLogger(__name__)

MIGRATIONS_ROOT = Path(__file__).parent / "migrations"


def _migrations_dir(kind: str) -> Path:
    specific = MIGRATIONS_ROOT / kind
    if specific.is_dir():
        return specific
    return MIGRATIONS_ROOT  # legacy flat layout (SQLite)


async def run_migrations(driver: StoreDriver) -> None:
    """Apply pending migrations in order. Idempotent."""
    # Tracker table. Same DDL on both backends (TEXT works in MySQL too).
    if driver.kind == "mysql":
        await driver.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {driver.t("_migrations")} (
                version     VARCHAR(64) PRIMARY KEY,
                applied_at  VARCHAR(40) NOT NULL
            )
            """
        )
    else:
        await driver.execute(
            """
            CREATE TABLE IF NOT EXISTS _migrations (
                version     TEXT PRIMARY KEY,
                applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
    await driver.commit()

    # Gather already-applied versions
    rows = await driver.fetchall("SELECT version FROM _migrations")
    applied = {row["version"] for row in rows}

    mig_dir = _migrations_dir(driver.kind)
    if not mig_dir.is_dir():
        logger.warning("[migrate] migrations dir not found: %s", mig_dir)
        return

    sql_files = sorted(mig_dir.glob("*.sql"))
    if not sql_files:
        logger.warning("[migrate] no migration files in %s", mig_dir)
        return

    for sql_file in sql_files:
        version = sql_file.stem  # "001_init"
        if version in applied:
            logger.debug("[migrate] already applied: %s", version)
            continue

        logger.info("[migrate] applying: %s (%s)", version, driver.kind)
        sql_text = sql_file.read_text(encoding="utf-8")
        await driver.executescript(sql_text)
        now = datetime.now(timezone.utc).isoformat()
        await driver.execute(
            "INSERT INTO _migrations (version, applied_at) VALUES (?, ?)",
            (version, now),
        )
        await driver.commit()
        logger.info("[migrate] applied: %s", version)
