"""Disk cache for OData responses.

Replaces ``lib/cachedcall.m``.  Keyed on the full query URL, stored as JSON
under one directory (the default, ``~/.cache/sbfsem``, holds every volume's
answers together) so a cache can be shipped alongside an offline dataset.

An entry never expires unless it is asked for with an age limit
(:meth:`DiskCache.get`'s ``max_age``, or the cache's own ``ttl``); the client
asks for one only for the lists that grow.  Nothing bounds the cache's size:
it held 3.0 GB in 5,952 files on the development machine on 2026-10-16.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from pathlib import Path

__all__ = ["DiskCache", "default_cache_dir"]


def default_cache_dir() -> Path:
    """``$SBFSEM_CACHE_DIR`` if set, else a platform-appropriate user cache."""
    env = os.environ.get("SBFSEM_CACHE_DIR")
    if env:
        return Path(env).expanduser()
    base = os.environ.get("XDG_CACHE_HOME")
    if base:
        return Path(base) / "sbfsem"
    return Path.home() / ".cache" / "sbfsem"


class DiskCache:
    """A tiny JSON blob store keyed by URL."""

    def __init__(self, directory: str | Path | None = None, ttl: float | None = None,
                 enabled: bool = True):
        self.directory = Path(directory) if directory is not None else default_cache_dir()
        self.ttl = ttl
        self.enabled = enabled

    def _path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        return self.directory / digest[:2] / f"{digest}.json"

    def get(self, url: str, max_age: float | None = None):
        """The cached document, or ``None``: absent, unreadable, or older than
        ``max_age`` seconds (the cache's ``ttl`` when none is given)."""
        if not self.enabled:
            return None
        p = self._path(url)
        if not p.is_file():
            return None
        limit = max_age if max_age is not None else self.ttl
        try:
            if limit is not None and (time.time() - p.stat().st_mtime) > limit:
                return None
        except OSError:
            return None
        try:
            with p.open("r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError):
            return None
        return payload.get("data")

    def set(self, url: str, data) -> None:
        if not self.enabled:
            return
        p = self._path(url)
        p.parent.mkdir(parents=True, exist_ok=True)
        # A name of its own per writer: two requests storing one URL at once
        # shared one temporary file, and the first to finish moved the second's
        # half-written bytes into place.
        tmp = p.with_name(f"{p.stem}.{uuid.uuid4().hex[:12]}.tmp")
        try:
            with tmp.open("w", encoding="utf-8") as fh:
                json.dump({"url": url, "data": data}, fh)
            tmp.replace(p)
        except OSError:  # pragma: no cover - best effort only
            tmp.unlink(missing_ok=True)

    def clear(self) -> int:
        """Delete every cached response.  Returns the number of files removed."""
        n = 0
        if self.directory.is_dir():
            for p in self.directory.rglob("*.json"):
                p.unlink(missing_ok=True)
                n += 1
        return n
