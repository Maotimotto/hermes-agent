"""
Storage driver abstraction.

Two backends:
  - SqliteDriver   (default, dev/test, no extra deps)
  - MysqlDriver    (production, requires `aiomysql`)

URL scheme picks the driver:
  - sqlite:///abs/path.db        → SQLite at that path (default if URL absent)
  - mysql://user:pwd@h:p/db?prefix=hcp_   → MySQL with optional table prefix

CRUD callers use:
  - driver.execute(sql, params)           – returns cursor-like object
  - driver.fetchone(sql, params)          – dict | None
  - driver.fetchall(sql, params)          – list[dict]
  - driver.executemany(sql, list_params)
  - driver.executescript(sql_text)
  - driver.commit()
  - driver.close()
  - driver.t("sessions")                  – returns prefixed table name

SQL templates use `?` placeholders and bare table names. The driver
rewrites both for MySQL: `?` → `%s`, and the table name in
`FROM/INTO/UPDATE/JOIN` clauses gets the prefix applied via `driver.t(...)`.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Iterable, Optional, Protocol, runtime_checkable
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger(__name__)


# Tables this store owns. Used by the driver to apply prefix in raw SQL.
KNOWN_TABLES = ("sessions", "turns", "events", "approvals", "_migrations")


# ── Protocol ─────────────────────────────────────────────────────────────────

@runtime_checkable
class StoreDriver(Protocol):
    kind: str  # "sqlite" | "mysql"

    def t(self, table: str) -> str: ...
    async def execute(self, sql: str, params: Iterable[Any] = ()) -> Any: ...
    async def executemany(
        self, sql: str, params_list: Iterable[Iterable[Any]]
    ) -> Any: ...
    async def executescript(self, sql_text: str) -> None: ...
    async def fetchone(
        self, sql: str, params: Iterable[Any] = ()
    ) -> dict | None: ...
    async def fetchall(
        self, sql: str, params: Iterable[Any] = ()
    ) -> list[dict]: ...
    async def commit(self) -> None: ...
    async def close(self) -> None: ...


# ── SQLite implementation ────────────────────────────────────────────────────

class SqliteDriver:
    """aiosqlite-backed driver. Tables are NOT prefixed (default)."""

    kind = "sqlite"

    def __init__(self, conn):
        import aiosqlite  # noqa: F401  (ensures import is at runtime)
        self._conn = conn
        self._prefix = ""

    def t(self, table: str) -> str:
        return table  # SQLite: no prefix

    def _prep(self, sql: str) -> str:
        return sql  # SQLite uses '?' natively

    async def execute(self, sql, params=()):
        return await self._conn.execute(self._prep(sql), tuple(params))

    async def executemany(self, sql, params_list):
        return await self._conn.executemany(
            self._prep(sql), [tuple(p) for p in params_list]
        )

    async def executescript(self, sql_text):
        await self._conn.executescript(sql_text)

    async def fetchone(self, sql, params=()):
        cursor = await self.execute(sql, params)
        row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def fetchall(self, sql, params=()):
        cursor = await self.execute(sql, params)
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def commit(self):
        await self._conn.commit()

    async def close(self):
        await self._conn.close()

    @property
    def raw(self):
        """Access underlying aiosqlite.Connection (escape hatch)."""
        return self._conn


# ── MySQL implementation ─────────────────────────────────────────────────────

# Match table identifiers in FROM/INTO/UPDATE/JOIN. We deliberately keep this
# tiny — call sites use only the KNOWN_TABLES set.
_TABLE_RX = re.compile(
    r"(?<![A-Za-z0-9_])(?P<kw>(?:FROM|INTO|UPDATE|JOIN)\s+)(?P<name>[A-Za-z_][A-Za-z0-9_]*)",
    re.IGNORECASE,
)


class MysqlDriver:
    """aiomysql-backed driver with optional table prefix.

    Uses a connection pool to keep concurrent coroutines safe — a single
    aiomysql connection can NOT be shared across awaiting tasks. Each
    execute()/fetchone()/fetchall() acquires a connection from the pool,
    runs the statement, and releases it. commit() is implicit on cursor
    close because we open a fresh connection per logical call when no
    explicit transaction is in flight.

    For multi-statement transactions, use a single connection via the
    pool acquire context manager (not exposed here — call sites are
    single-statement).
    """

    kind = "mysql"

    def __init__(self, pool, prefix: str = ""):
        self._pool = pool
        self._prefix = prefix or ""

    def t(self, table: str) -> str:
        return f"{self._prefix}{table}"

    def _prep(self, sql: str) -> str:
        prepared = sql.replace("?", "%s")
        if self._prefix:
            def _sub(m):
                name = m.group("name")
                if name in KNOWN_TABLES:
                    return f"{m.group('kw')}{self._prefix}{name}"
                return m.group(0)
            prepared = _TABLE_RX.sub(_sub, prepared)
        return prepared

    async def execute(self, sql, params=()):
        """Single-statement execute with auto-commit on the borrowed conn.

        Returns a *materialized* cursor-like shim:
          - .lastrowid
          - .fetchone() / .fetchall() that yield the in-memory rows
        Because the underlying conn is released back to the pool before
        this returns, callers must not stream rows lazily.
        """
        async with self._pool.acquire() as conn:
            cur = await conn.cursor()
            try:
                await cur.execute(self._prep(sql), tuple(params))
                # Snapshot results before releasing the connection.
                fetched = None
                if cur.description is not None:
                    fetched = await cur.fetchall()
                await conn.commit()
                return _MysqlResult(
                    lastrowid=cur.lastrowid,
                    rowcount=cur.rowcount,
                    rows=fetched,
                )
            finally:
                await cur.close()

    async def executemany(self, sql, params_list):
        async with self._pool.acquire() as conn:
            cur = await conn.cursor()
            try:
                await cur.executemany(
                    self._prep(sql), [tuple(p) for p in params_list]
                )
                await conn.commit()
                return _MysqlResult(
                    lastrowid=cur.lastrowid,
                    rowcount=cur.rowcount,
                    rows=None,
                )
            finally:
                await cur.close()

    async def executescript(self, sql_text):
        """Execute multi-statement DDL. Splits on ';' (naïve but DDL-safe)."""
        if self._prefix:
            sql_text = self._apply_prefix_to_ddl(sql_text)
        statements = [s for s in (s.strip() for s in self._split_sql(sql_text)) if s]
        async with self._pool.acquire() as conn:
            cur = await conn.cursor()
            try:
                for stmt in statements:
                    await cur.execute(stmt)
                await conn.commit()
            finally:
                await cur.close()

    def _apply_prefix_to_ddl(self, sql_text: str) -> str:
        """Rewrite CREATE TABLE / INDEX statements to use prefixed names."""
        sql_text = re.sub(
            r"(CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?)([A-Za-z_][A-Za-z0-9_]*)",
            lambda m: f"{m.group(1)}{self._prefix if m.group(2) in KNOWN_TABLES else ''}{m.group(2)}",
            sql_text,
            flags=re.IGNORECASE,
        )
        sql_text = re.sub(
            r"(?<![A-Za-z0-9_])(ON\s+)([A-Za-z_][A-Za-z0-9_]*)",
            lambda m: f"{m.group(1)}{self._prefix if m.group(2) in KNOWN_TABLES else ''}{m.group(2)}",
            sql_text,
            flags=re.IGNORECASE,
        )
        sql_text = re.sub(
            r"(?<![A-Za-z0-9_])(REFERENCES\s+)([A-Za-z_][A-Za-z0-9_]*)",
            lambda m: f"{m.group(1)}{self._prefix if m.group(2) in KNOWN_TABLES else ''}{m.group(2)}",
            sql_text,
            flags=re.IGNORECASE,
        )
        # CONSTRAINT <name>  — MySQL CHECK + FK constraint names are
        # schema-scoped, so we have to prefix them too to allow multiple
        # prefixed installs to coexist in the same database.
        sql_text = re.sub(
            r"(\bCONSTRAINT\s+)([A-Za-z_][A-Za-z0-9_]*)",
            lambda m: f"{m.group(1)}{self._prefix}{m.group(2)}",
            sql_text,
            flags=re.IGNORECASE,
        )
        # CREATE INDEX <name> ON …  — index names are scoped per-table in
        # MySQL but we prefix anyway for symmetry with the table prefix.
        sql_text = re.sub(
            r"(CREATE\s+(?:UNIQUE\s+)?INDEX\s+(?:IF\s+NOT\s+EXISTS\s+)?)([A-Za-z_][A-Za-z0-9_]*)",
            lambda m: f"{m.group(1)}{self._prefix}{m.group(2)}",
            sql_text,
            flags=re.IGNORECASE,
        )
        return sql_text

    @staticmethod
    def _split_sql(sql_text: str) -> list[str]:
        """Split SQL text on ';' boundaries while respecting `--` comments."""
        out: list[str] = []
        buf: list[str] = []
        for line in sql_text.splitlines():
            stripped = line.strip()
            if stripped.startswith("--"):
                continue
            buf.append(line)
            if stripped.endswith(";"):
                out.append("\n".join(buf).rstrip(";\n "))
                buf = []
        tail = "\n".join(buf).strip()
        if tail:
            out.append(tail)
        return out

    async def fetchone(self, sql, params=()):
        result = await self.execute(sql, params)
        return result.fetchone()

    async def fetchall(self, sql, params=()):
        result = await self.execute(sql, params)
        return result.fetchall()

    async def commit(self):
        # No-op: each execute auto-commits on its borrowed connection.
        return None

    async def close(self):
        self._pool.close()
        await self._pool.wait_closed()


class _MysqlResult:
    """Materialized result shim — connection is already released."""

    def __init__(self, lastrowid, rowcount, rows):
        self.lastrowid = lastrowid
        self.rowcount = rowcount
        self._rows = rows or []
        self._iter = 0

    def fetchone(self):
        if self._rows is None:
            return None
        if self._iter >= len(self._rows):
            return None
        row = self._rows[self._iter]
        self._iter += 1
        return row

    def fetchall(self):
        return list(self._rows or [])


# ── URL parsing + factory ────────────────────────────────────────────────────

DEFAULT_DB_PATH = Path.home() / ".hermes" / "control_plane.db"


def _normalize_url(url: str | Path | None) -> str:
    """Turn a path-like or URL-like value into a real URL.

    None       → sqlite:///~/.hermes/control_plane.db
    Path/str   → sqlite:///abs/path  (when it doesn't already look like a URL)
    URL        → returned verbatim
    """
    if url is None:
        return f"sqlite:///{DEFAULT_DB_PATH}"
    s = str(url)
    if "://" in s:
        return s
    # treat as filesystem path
    return f"sqlite:///{Path(s).expanduser()}"


async def create_driver(url: str | Path | None = None) -> StoreDriver:
    """Open a connection and wrap it in the appropriate driver.

    For SQLite: enables WAL, foreign_keys, busy_timeout.
    For MySQL : opens via aiomysql, uses DictCursor, applies prefix from
                the URL's `?prefix=...` query parameter (default "").
    """
    normalized = _normalize_url(url)
    parsed = urlparse(normalized)
    scheme = parsed.scheme.lower()

    if scheme == "sqlite":
        return await _open_sqlite(parsed)
    if scheme in ("mysql", "mysql+aiomysql"):
        return await _open_mysql(parsed)

    raise ValueError(f"Unsupported store URL scheme: {scheme!r}")


async def _open_sqlite(parsed) -> SqliteDriver:
    import aiosqlite

    # urlparse("sqlite:///abs/path") → netloc="", path="/abs/path"
    path_str = (parsed.netloc or "") + parsed.path
    path = Path(path_str)
    if not path.is_absolute():
        # urls like sqlite:///~/foo.db get expanded
        path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)

    conn = await aiosqlite.connect(str(path))
    await conn.execute("PRAGMA journal_mode = WAL")
    await conn.execute("PRAGMA foreign_keys = ON")
    await conn.execute("PRAGMA busy_timeout = 5000")
    conn.row_factory = aiosqlite.Row
    return SqliteDriver(conn)


async def _open_mysql(parsed) -> MysqlDriver:
    try:
        import aiomysql
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "aiomysql not installed — `pip install aiomysql` to use the MySQL backend"
        ) from exc

    user = parsed.username or ""
    password = parsed.password or ""
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 3306
    db = (parsed.path or "/").lstrip("/")

    qs = parse_qs(parsed.query)
    prefix = (qs.get("prefix", [""])[0] or "").strip()
    pool_min = int(qs.get("pool_min", ["1"])[0])
    pool_max = int(qs.get("pool_max", ["10"])[0])

    pool = await aiomysql.create_pool(
        host=host,
        port=port,
        user=user,
        password=password,
        db=db,
        autocommit=False,
        charset="utf8mb4",
        cursorclass=aiomysql.DictCursor,
        minsize=pool_min,
        maxsize=pool_max,
    )
    return MysqlDriver(pool, prefix=prefix)
