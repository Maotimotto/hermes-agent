"""
Backwards-compat shim — opens a connection via the driver factory.

Older callers do `from .db import create_database` and pass an `aiosqlite.Connection`
into the CRUD modules. New callers should use `create_driver()` directly via
`SessionStore`. We keep this module thin and forward to driver.py.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .driver import (
    DEFAULT_DB_PATH,
    create_driver,
    StoreDriver,
)

logger = logging.getLogger(__name__)


async def create_database(
    db_path: str | Path | None = None,
) -> StoreDriver:
    """Open a store driver. URL scheme picks SQLite vs MySQL.

    Honors `HERMES_CP_DB_URL` env var when no explicit value is passed. Returns
    a `StoreDriver` (sqlite or mysql) — callers should treat it via the driver
    interface, not as an aiosqlite connection.
    """
    if db_path is None:
        db_path = os.environ.get("HERMES_CP_DB_URL")
    return await create_driver(db_path)
