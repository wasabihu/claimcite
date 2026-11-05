"""On-disk cache for registry responses. Standard library only.

Wraps the ``http_get(url) -> str`` callable: successful responses are stored by
URL (which encodes registry + identifier), so a second run after a one-line edit
does not repeat every request. Lookup failures are never written.
"""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

DB_NAME = "cache.sqlite3"
DEFAULT_TTL_DAYS = 30


def default_cache_dir() -> Path:
    """Honour XDG_CACHE_HOME; fall back to ~/.cache/claimcite/v1."""
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "claimcite" / "v1"


class CachedHttp:
    """A thread-safe, file-backed wrapper around an HTTP callable."""

    def __init__(
        self,
        http: Callable[[str], str],
        cache_dir: Path | None = None,
        ttl_days: float = DEFAULT_TTL_DAYS,
    ):
        self.http = http
        self.cache_dir = cache_dir if cache_dir is not None else default_cache_dir()
        self.ttl = ttl_days * 86400
        self._init_db()

    @property
    def db_path(self) -> Path:
        return self.cache_dir / DB_NAME

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=5.0)

    def _init_db(self) -> None:
        # The host shim raises FileExistsError on mkdir(exist_ok=True) for an
        # existing directory, so check first instead.
        if not self.cache_dir.is_dir():
            self.cache_dir.mkdir(parents=True)
        db = self._connect()
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS responses ("
                "url TEXT PRIMARY KEY, body TEXT NOT NULL, fetched_at REAL NOT NULL)"
            )
            db.commit()
        finally:
            db.close()

    def __call__(self, url: str) -> str:
        db = self._connect()
        try:
            row = db.execute("SELECT body, fetched_at FROM responses WHERE url = ?", (url,)).fetchone()
        finally:
            db.close()
        if row is not None and time.time() - row[1] <= self.ttl:
            return row[0]

        # Exceptions propagate untouched: a failed lookup is never cached.
        body = self.http(url)

        db = self._connect()
        try:
            db.execute(
                "INSERT OR REPLACE INTO responses (url, body, fetched_at) VALUES (?, ?, ?)",
                (url, body, time.time()),
            )
            db.commit()
        finally:
            db.close()
        return body
