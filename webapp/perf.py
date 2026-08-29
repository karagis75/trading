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
import threading
import time
from typing import Any, Callable


# ── TTL constants ─────────────────────────────────────────────────────────────
SHORT_TTL = 60          # 1 min — live/summary data (latest date, health strip)
LIVE_TTL = 300          # 5 min — scanner index and stock search universe
HISTORICAL_TTL = 86400  # 24 h  — past day pages never change


# ── Thread-local DB connections ───────────────────────────────────────────────
_local = threading.local()


def thread_db(database_url: str):
    """Return a cached per-thread connection, creating one if needed.

    We keep one SQLite connection open per thread so we avoid the per-request
    open/close/schema-check cost (~1 ms per connection on a warm disk).
    The connection is stored on threading.local so it is safe under Flask's
    threaded WSGI server.
    """
    key = f"_db_{hashlib.md5(database_url.encode()).hexdigest()}"
    conn = getattr(_local, key, None)
    if conn is None:
        from scanner_history import db as _db
        conn = _db.connect(database_url)
        # Larger page cache (8 MB) so frequently read pages stay in memory.
        if hasattr(conn, "execute"):
            try:
                conn.execute("PRAGMA cache_size = -8192")   # 8 MB
                conn.execute("PRAGMA temp_store = MEMORY")
            except Exception:
                pass
        setattr(_local, key, conn)
    return conn


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
