"""Lightweight in-process cache and thread-local DB connection pool.

Goals
-----
* Scanner day pages and stock history are **immutable once written** — the daily
  runner only writes after all jobs finish, then never re-writes the same date.
  Cache them aggressively (HISTORICAL_TTL).
* Live summary data (latest date, scanner index, run health) changes at most
  once per day — cache for SHORT_TTL seconds so a page refresh after the daily
  run shows fresh data within a minute without reopening the DB.
* The SQLite connection is kept open per OS thread (thread-local) with WAL mode
  and a larger page-cache, saving the per-request open/close overhead.
"""

from __future__ import annotations

import hashlib
import sqlite3
import threading
import time
from typing import Any, Callable


# ── TTL constants ─────────────────────────────────────────────────────────────
SHORT_TTL = 60          # 1 min — live/summary data (latest date, health strip)
LIVE_TTL = 300          # 5 min — scanner index and stock search universe
HISTORICAL_TTL = 86400  # 24 h  — past day pages never change


# ── Thread-local DB connections ───────────────────────────────────────────────
_local = threading.local()


def _cache_key(database_url: str) -> str:
    return f"_db_{hashlib.md5(database_url.encode()).hexdigest()}"


def thread_db(database_url: str):
    """Return a cached per-thread connection, creating one if needed.

    We keep one connection open per thread so we avoid the per-request
    open/close/schema-check cost (~1 ms per connection on a warm disk).
    The connection is stored on threading.local so it is safe under Flask's
    threaded WSGI server.
    """
    key = _cache_key(database_url)
    conn = getattr(_local, key, None)
    if conn is None:
        from scanner_history import db as _db
        is_postgres = database_url.startswith(("postgresql://", "postgres://"))
        # Read-only PostgreSQL connections use autocommit so each query ends
        # immediately and no idle transaction persists across web requests.
        # SQLite ignores this option inside db.connect().
        conn = _db.connect(database_url, autocommit=is_postgres)
        # SQLite-only tuning. PRAGMA statements are invalid SQL on PostgreSQL
        # and, sent over a non-autocommit connection, would abort the
        # transaction — poisoning this cached connection for every future
        # request on this thread. Only apply to real sqlite3 connections.
        if isinstance(conn, sqlite3.Connection):
            try:
                conn.execute("PRAGMA cache_size = -8192")   # 8 MB
                conn.execute("PRAGMA temp_store = MEMORY")
            except Exception:
                pass
        setattr(_local, key, conn)
    return conn


class LazyConnection:
    """Defers acquiring the thread-local DB connection until first use.

    Why this matters: the dev server runs with ``threaded=True`` so a page's
    HTML/CSS/JS/favicon requests are handled concurrently instead of queueing
    behind each other. Werkzeug's threaded mode (``socketserver.ThreadingMixIn``)
    spawns a **new OS thread per connection** rather than reusing a pool, so if
    a connection were opened eagerly on every request, most requests would pay
    for a fresh connection (a real cost for PostgreSQL: a new TCP handshake and
    backend process per request) even when the page is fully served from the
    in-process cache in ``cached()`` below and never actually queries the DB.

    With this wrapper, `get_db()` is free to call any time — the real
    connection is only opened the first time a query actually executes, so a
    request served entirely from cache opens zero DB connections.
    """

    __slots__ = ("_database_url", "_conn")

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._conn = None

    def _resolve(self):
        if self._conn is None:
            self._conn = thread_db(self._database_url)
        return self._conn

    def execute(self, *args, **kwargs):
        return self._resolve().execute(*args, **kwargs)

    def executemany(self, *args, **kwargs):
        return self._resolve().executemany(*args, **kwargs)

    def commit(self):
        return self._resolve().commit()

    def close(self) -> None:
        # Thread-local connections are reset via reset_thread_db(), not
        # closed per-request; nothing to do if this wrapper never resolved.
        pass


def reset_thread_db(database_url: str) -> None:
    """Drop this thread's cached connection so the next request opens a fresh one.

    Call this after a request fails while using the DB — a persistent
    connection can be left unusable (e.g. an aborted PostgreSQL transaction),
    and without this the whole process would 500 forever until restarted.
    """
    key = _cache_key(database_url)
    conn = getattr(_local, key, None)
    if conn is not None:
        try:
            conn.close()
        except Exception:
            pass
        try:
            delattr(_local, key)
        except AttributeError:
            pass


# ── Simple TTL cache ──────────────────────────────────────────────────────────
_cache: dict[str, tuple[float, Any]] = {}
_cache_lock = threading.Lock()


def cached(key: str, ttl: float, fn: Callable[[], Any]) -> Any:
    """Return a cached value, calling *fn* only when the entry is missing or stale."""
    now = time.monotonic()
    with _cache_lock:
        entry = _cache.get(key)
        if entry is not None and now - entry[0] < ttl:
            return entry[1]
    value = fn()
    with _cache_lock:
        _cache[key] = (now, value)
    return value


def invalidate(prefix: str = "") -> None:
    """Drop all cache entries whose key starts with *prefix* (or all if empty)."""
    with _cache_lock:
        if not prefix:
            _cache.clear()
        else:
            for key in list(_cache):
                if key.startswith(prefix):
                    del _cache[key]
