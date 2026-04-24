"""Scene cache — hash by location, characters, mood, seed.

Caches rendered scene images on disk with JSON sidecar metadata.
Uses LRU eviction when the cache exceeds its configured max size.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections import OrderedDict
from pathlib import Path

from sidequest.renderer.models import RenderResult, StageCue


def cache_key(cue: StageCue) -> str:
    """Compute a deterministic hex digest from scene parameters.

    The key is derived from (location, sorted(characters), mood, seed).
    Character order is irrelevant — the list is sorted before hashing.
    """
    parts = (
        cue.tier.value,
        cue.location,
        tuple(sorted(cue.characters)),
        cue.mood,
        cue.seed,
    )
    return hashlib.sha256(repr(parts).encode()).hexdigest()


class SceneCache:
    """Disk-backed scene cache with LRU eviction."""

    def __init__(self, *, cache_dir: Path, max_size_bytes: int) -> None:
        self._cache_dir = cache_dir
        self._max_size_bytes = max_size_bytes
        # OrderedDict tracks access order for LRU: key -> file size (image + sidecar)
        self._entries: OrderedDict[str, int] = OrderedDict()
        self._total_size = 0

    @property
    def current_size_bytes(self) -> int:
        return self._total_size

    def _ensure_dir(self) -> None:
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def _image_path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.png"

    def _sidecar_path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.json"

    def _entry_size(self, key: str) -> int:
        """Image bytes for an entry (sidecar metadata not counted)."""
        img = self._image_path(key)
        if img.exists():
            return img.stat().st_size
        return 0

    def _evict(self) -> None:
        """Remove least-recently-used entries until under max size."""
        while self._total_size > self._max_size_bytes and self._entries:
            oldest_key, oldest_size = next(iter(self._entries.items()))
            self._remove_files(oldest_key)
            self._entries.pop(oldest_key)
            self._total_size -= oldest_size

    def _remove_files(self, key: str) -> None:
        for path in (self._image_path(key), self._sidecar_path(key)):
            path.unlink(missing_ok=True)

    def put(self, cue: StageCue, result: RenderResult) -> None:
        """Store a render result in the cache."""
        self._ensure_dir()
        key = cache_key(cue)

        # Remove existing entry if overwriting
        if key in self._entries:
            self._total_size -= self._entries.pop(key)
            self._remove_files(key)

        # Copy image to cache
        shutil.copy2(result.image_path, self._image_path(key))

        # Write JSON sidecar
        sidecar_data = {
            "location": cue.location,
            "characters": list(cue.characters),
            "mood": cue.mood,
            "seed": cue.seed,
            "tier": cue.tier.value,
            "subject": cue.subject,
            "width": result.width,
            "height": result.height,
            "generation_time_ms": result.generation_time_ms,
            "worker": result.worker,
        }
        self._sidecar_path(key).write_text(
            json.dumps(sidecar_data, separators=(",", ":"))
        )

        # Track entry
        entry_size = self._entry_size(key)
        self._entries[key] = entry_size
        self._total_size += entry_size

        # Evict if over budget
        self._evict()

    def get(self, cue: StageCue) -> RenderResult | None:
        """Look up a cached scene. Returns None on miss or corrupt data."""
        key = cache_key(cue)

        if key not in self._entries:
            return None

        img_path = self._image_path(key)
        sidecar_path = self._sidecar_path(key)

        if not img_path.exists():
            return None

        try:
            data = json.loads(sidecar_path.read_text())
            result = RenderResult(
                image_path=img_path,
                width=data["width"],
                height=data["height"],
                generation_time_ms=data["generation_time_ms"],
                tier=cue.tier,
                cue=cue,
                worker="cache",
            )
        except (
            json.JSONDecodeError,
            KeyError,
            UnicodeDecodeError,
            FileNotFoundError,
            OSError,
        ):
            return None

        # Move to end (most recently used)
        self._entries.move_to_end(key)

        return result

    def get_path(self, cue: StageCue) -> Path | None:
        """Return the cached image path without reading bytes. None on miss."""
        key = cache_key(cue)
        if key not in self._entries:
            return None
        img_path = self._image_path(key)
        if not img_path.exists():
            return None
        return img_path

    def clear(self) -> None:
        """Remove all cached entries."""
        for key in list(self._entries):
            self._remove_files(key)
        self._entries.clear()
        self._total_size = 0
