"""On-disk cache for registry responses. Standard library only.

Wraps the ``http_get(url) -> str`` callable: successful responses are stored by
URL (which encodes registry + identifier), so a second run after a one-line edit
does not repeat every request. Lookup failures are never written.

The cache is best-effort: if the directory or database cannot be used (read-only
home, full disk, locked file), requests go straight to the network.
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
import time
import zlib
from collections.abc import Callable
from pathlib import Path

DB_NAME = "cache.sqlite3"
DEFAULT_TTL_DAYS = 30
# Credentials must not be written to disk, and changing them must not empty the cache.
_SECRET_PARAM = re.compile(r"[?&]api_key=[^&]*")


def cache_key(url: str) -> str:
    return _SECRET_PARAM.sub("", url)


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
        self.enabled = True
        try:
            self._init_db()
        except (OSError, sqlite3.Error) as e:
            self._disable(e)

    def _disable(self, error: Exception) -> None:
        if self.enabled:
            print(f"claimcite: cache disabled ({error})", file=sys.stderr)
        self.enabled = False

    @property
    def db_path(self) -> Path:
        return self.cache_dir / DB_NAME

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=5.0)

    def _init_db(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        db = self._connect()
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS responses ("
                "url TEXT PRIMARY KEY, body BLOB NOT NULL, fetched_at REAL NOT NULL)"
            )
            db.commit()
        finally:
            db.close()

    def __call__(self, url: str) -> str:
        if not self.enabled:
            return self.http(url)
        key = cache_key(url)
        try:
            db = self._connect()
            try:
                row = db.execute("SELECT body, fetched_at FROM responses WHERE url = ?", (key,)).fetchone()
            finally:
                db.close()
        except sqlite3.Error as e:
            self._disable(e)
            return self.http(url)
        if row is not None and time.time() - row[1] <= self.ttl:
            body = row[0]
            # Bodies are stored compressed; plain text rows are from before that change.
            return zlib.decompress(body).decode("utf-8") if isinstance(body, bytes) else body

        # Exceptions propagate untouched: a failed lookup is never cached.
        body = self.http(url)

        try:
            db = self._connect()
            try:
                db.execute(
                    "INSERT OR REPLACE INTO responses (url, body, fetched_at) VALUES (?, ?, ?)",
                    (key, zlib.compress(body.encode("utf-8")), time.time()),
                )
                db.commit()
            finally:
                db.close()
        except sqlite3.Error as e:
            self._disable(e)
        return body
