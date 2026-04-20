"""Genre pack cache — load once, return same instance for same code.

Port of sidequest-genre/src/cache.rs (~48 LOC).

Rust uses Arc<GenrePack> for shared ownership across threads; Python uses a
plain dict protected by threading.Lock. The Loader is injected on get_or_load
rather than stored, matching the Rust signature.

The GenrePack type is not yet ported (subagent B). GenreCache is written
against a forward-declared protocol so it can be imported now and populated
when subagent B lands the GenrePack model.
"""

from __future__ import annotations

import threading
from typing import Any


class GenreCache:
    """Thread-safe cache for loaded genre packs.

    Returns the same object for repeated loads of the same genre code.
    Port of Rust GenreCache struct (cache.rs).

    The cache key is the genre code string. The loader callable must accept
    a code string and return a GenrePack (or equivalent) or raise GenreError.
    """

    def __init__(self) -> None:
        self._packs: dict[str, Any] = {}
        self._lock = threading.Lock()

    def get_or_load(self, code: str, loader: Any) -> Any:
        """Get a cached pack or load it via the loader.

        If the genre code has been loaded before, returns the cached object.
        Otherwise, loads from disk via loader.load(code) and caches the result.

        Args:
            code: Genre pack code string (e.g. "caverns_and_claudes").
            loader: Object with a .load(code: str) -> GenrePack method.
                    Port of Rust GenreLoader.

        Returns:
            The GenrePack for this code.

        Raises:
            GenreError: Propagated from loader.load() on failure.
        """
        with self._lock:
            if code in self._packs:
                return self._packs[code]
            pack = loader.load(code)
            self._packs[code] = pack
            return pack

    def invalidate(self, code: str) -> None:
        """Remove a single code from the cache, forcing a reload on next access.

        No Rust equivalent (Rust cache is append-only for the process lifetime);
        added here because Python tests need cache isolation between test runs.
        """
        with self._lock:
            self._packs.pop(code, None)

    def clear(self) -> None:
        """Evict all cached packs.

        Added for test isolation; not in the Rust original.
        """
        with self._lock:
            self._packs.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._packs)
