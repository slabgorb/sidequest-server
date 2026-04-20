"""Tests for GenreCache.

Port of sidequest-genre/src/cache.rs semantics.
"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock

import pytest

from sidequest.genre.cache import GenreCache
from sidequest.genre.error import GenreNotFoundError


class _FakePack:
    """Minimal stand-in for GenrePack (subagent B will land the real model)."""

    def __init__(self, code: str) -> None:
        self.code = code


class _FakeLoader:
    """Loader that counts calls and returns _FakePack instances."""

    def __init__(self) -> None:
        self.call_count = 0

    def load(self, code: str) -> _FakePack:
        self.call_count += 1
        return _FakePack(code)


class _ErrorLoader:
    """Loader that always raises GenreNotFoundError."""

    def load(self, code: str) -> _FakePack:
        raise GenreNotFoundError(code=code, searched=["/nowhere"])


# ---------------------------------------------------------------------------
# Basic get_or_load semantics
# ---------------------------------------------------------------------------


def test_cache_loads_pack_on_first_access() -> None:
    """get_or_load calls loader.load exactly once for a new code."""
    cache = GenreCache()
    loader = _FakeLoader()
    pack = cache.get_or_load("caverns_and_claudes", loader)
    assert pack.code == "caverns_and_claudes"
    assert loader.call_count == 1


def test_cache_returns_same_object_on_second_access() -> None:
    """get_or_load returns the cached object on repeated calls."""
    cache = GenreCache()
    loader = _FakeLoader()
    pack1 = cache.get_or_load("caverns_and_claudes", loader)
    pack2 = cache.get_or_load("caverns_and_claudes", loader)
    assert pack1 is pack2
    assert loader.call_count == 1


def test_cache_loads_different_codes_independently() -> None:
    """Different codes produce separate cache entries."""
    cache = GenreCache()
    loader = _FakeLoader()
    a = cache.get_or_load("caverns_and_claudes", loader)
    b = cache.get_or_load("road_warrior", loader)
    assert a.code == "caverns_and_claudes"
    assert b.code == "road_warrior"
    assert loader.call_count == 2


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------


def test_cache_propagates_loader_error() -> None:
    """get_or_load propagates GenreNotFoundError from the loader."""
    cache = GenreCache()
    loader = _ErrorLoader()
    with pytest.raises(GenreNotFoundError, match="ghost_genre"):
        cache.get_or_load("ghost_genre", loader)


def test_cache_does_not_cache_failed_loads() -> None:
    """A failed load is not cached — next call retries via the loader."""
    cache = GenreCache()
    call_count = 0

    class _FlakyLoader:
        def load(self, code: str) -> _FakePack:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise GenreNotFoundError(code=code, searched=[])
            return _FakePack(code)

    loader = _FlakyLoader()
    with pytest.raises(GenreNotFoundError):
        cache.get_or_load("flaky", loader)
    # Second call should succeed (loader gets called again, not cached from failure)
    pack = cache.get_or_load("flaky", loader)
    assert pack.code == "flaky"
    assert call_count == 2


# ---------------------------------------------------------------------------
# Cache management
# ---------------------------------------------------------------------------


def test_cache_len_tracks_loaded_packs() -> None:
    cache = GenreCache()
    loader = _FakeLoader()
    assert len(cache) == 0
    cache.get_or_load("a", loader)
    assert len(cache) == 1
    cache.get_or_load("b", loader)
    assert len(cache) == 2


def test_cache_invalidate_forces_reload() -> None:
    cache = GenreCache()
    loader = _FakeLoader()
    pack1 = cache.get_or_load("code", loader)
    cache.invalidate("code")
    pack2 = cache.get_or_load("code", loader)
    # Different object — reloaded
    assert pack1 is not pack2
    assert loader.call_count == 2


def test_cache_clear_evicts_all() -> None:
    cache = GenreCache()
    loader = _FakeLoader()
    cache.get_or_load("a", loader)
    cache.get_or_load("b", loader)
    cache.clear()
    assert len(cache) == 0
    # Subsequent access reloads
    cache.get_or_load("a", loader)
    assert loader.call_count == 3


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------


def test_cache_thread_safety() -> None:
    """Concurrent get_or_load calls for the same key must not double-load."""
    cache = GenreCache()
    load_count = 0
    barrier = threading.Barrier(10)

    class _CountingLoader:
        def load(self, code: str) -> _FakePack:
            nonlocal load_count
            load_count += 1
            return _FakePack(code)

    loader = _CountingLoader()
    results: list[_FakePack] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        pack = cache.get_or_load("shared_code", loader)
        with lock:
            results.append(pack)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 10
    # All threads got the same object
    first = results[0]
    assert all(p is first for p in results)
    # Loader called exactly once
    assert load_count == 1
