"""
Validator-based page cache.

Stores extracted content plus HTTP validators (ETag, Last-Modified)
so repeat fetches can issue conditional requests. Falls back to
TTL-based expiry when validators are absent.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("pulldown")


def default_cache_dir() -> Path:
    """Return a platform-appropriate default cache directory."""
    # Prefer XDG_CACHE_HOME on Linux, %LOCALAPPDATA% on Windows, ~/Library/Caches on macOS.
    xdg = os.environ.get("XDG_CACHE_HOME")
    if xdg:
        return Path(xdg) / "pulldown"
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or tempfile.gettempdir()
        return Path(base) / "pulldown" / "cache"
    # macOS and generic POSIX
    home = Path.home()
    if (home / "Library" / "Caches").exists():
        return home / "Library" / "Caches" / "pulldown"
    return home / ".cache" / "pulldown"


class PageCache:
    """
    On-disk cache for fetched pages.

    Entries are stored as JSON files in ``cache_dir``, keyed by URL + detail level.
    Writes are atomic (temp file + ``os.replace``) so concurrent writers never
    see a half-written file. Entries carry HTTP validators (ETag/Last-Modified)
    when the origin supplied them; use :meth:`validators_for` to build
    conditional-request headers for a subsequent fetch.

    Usage
    -----
    >>> cache = PageCache(ttl=3600)
    >>> result = await fetch(url, cache=cache)
    """

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        ttl: int = 3600,
    ):
        self.cache_dir = Path(cache_dir) if cache_dir else default_cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl

    # ------------------------------------------------------------------
    # path helpers
    # ------------------------------------------------------------------

    def _key(self, url: str, detail: str) -> str:
        raw = f"{url}::{detail}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def _path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    # ------------------------------------------------------------------
    # reads
    # ------------------------------------------------------------------

    def get(self, url: str, detail: str) -> dict[str, Any] | None:
        """Return cached entry or None if expired/missing/corrupt."""
        key = self._key(url, detail)
        path = self._path(key)
        if not path.exists():
            return None

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            # Corrupt file — best effort clean up, then miss.
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            return None

        ts = data.get("_cached_at", 0)
        if time.time() - ts > self.ttl:
            # Stale: return None so caller re-fetches, but keep the entry
            # around so its validators (ETag/Last-Modified) can drive a
            # conditional request on the next fetch. Pruning is explicit.
            logger.debug("cache expired: %s", url)
            return None

        return data

    def get_stale(self, url: str, detail: str) -> dict[str, Any] | None:
        """Return the cached entry regardless of TTL. Used after a 304 response."""
        key = self._key(url, detail)
        path = self._path(key)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return None

    def validators_for(self, url: str, detail: str) -> dict[str, str]:
        """
        Return headers suitable for a conditional request, based on stored
        validators for this URL+detail. Empty dict if no validators are cached
        or if the entry is missing.
        """
        key = self._key(url, detail)
        path = self._path(key)
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return {}
        headers = {}
        etag = data.get("_etag")
        last_modified = data.get("_last_modified")
        if etag:
            headers["If-None-Match"] = etag
        if last_modified:
            headers["If-Modified-Since"] = last_modified
        return headers

    def touch(self, url: str, detail: str) -> None:
        """Update the cached_at timestamp on an existing entry (e.g. after 304)."""
        key = self._key(url, detail)
        path = self._path(key)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return
        data["_cached_at"] = time.time()
        self._atomic_write(path, data)

    # ------------------------------------------------------------------
    # writes
    # ------------------------------------------------------------------

    def put(
        self,
        url: str,
        detail: str,
        entry: dict[str, Any],
        *,
        etag: str | None = None,
        last_modified: str | None = None,
    ) -> None:
        """Store an entry in the cache, atomically."""
        key = self._key(url, detail)
        path = self._path(key)
        entry = dict(entry)  # don't mutate caller's dict
        entry["_cached_at"] = time.time()
        entry["_url"] = url
        entry["_detail"] = detail
        if etag:
            entry["_etag"] = etag
        if last_modified:
            entry["_last_modified"] = last_modified
        try:
            self._atomic_write(path, entry)
        except OSError as e:
            logger.warning("cache write failed: %s", e)

    def _atomic_write(self, path: Path, data: dict[str, Any]) -> None:
        """Write ``data`` as JSON to ``path`` atomically via temp file + rename."""
        tmp = path.with_suffix(path.suffix + ".tmp")
        payload = json.dumps(data, default=str, ensure_ascii=False)
        tmp.write_text(payload, encoding="utf-8")
        tmp.replace(path)

    # ------------------------------------------------------------------
    # invalidation
    # ------------------------------------------------------------------

    def invalidate(self, url: str, detail: str | None = None) -> int:
        """Remove cache entries for a URL. Returns count of removed entries."""
        removed = 0
        if detail:
            key = self._key(url, detail)
            path = self._path(key)
            if path.exists():
                path.unlink()
                removed = 1
        else:
            for d in ("minimal", "readable", "full", "raw"):
                key = self._key(url, d)
                path = self._path(key)
                if path.exists():
                    path.unlink()
                    removed += 1
        return removed

    def prune_expired(self) -> int:
        """Remove entries whose age exceeds the TTL. Returns count removed."""
        count = 0
        now = time.time()
        for f in self.cache_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                ts = data.get("_cached_at", 0)
                if now - ts > self.ttl:
                    f.unlink()
                    count += 1
            except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                # Corrupt entry — prune it.
                try:
                    f.unlink()
                    count += 1
                except OSError:
                    pass
        return count

    def clear(self) -> int:
        """Remove all cache entries. Returns count removed."""
        count = 0
        for f in self.cache_dir.glob("*.json"):
            f.unlink()
            count += 1
        return count

    def stats(self) -> dict[str, Any]:
        """Return cache stats."""
        files = list(self.cache_dir.glob("*.json"))
        total_bytes = sum(f.stat().st_size for f in files)
        return {
            "entries": len(files),
            "total_bytes": total_bytes,
            "cache_dir": str(self.cache_dir),
            "ttl": self.ttl,
        }
