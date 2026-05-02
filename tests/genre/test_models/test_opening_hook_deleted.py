"""Verifies OpeningHook and MpOpening are removed from the public API."""

from __future__ import annotations

import pytest


def test_opening_hook_no_longer_exported() -> None:
    with pytest.raises(ImportError):
        from sidequest.genre.models.narrative import OpeningHook  # noqa: F401


def test_mp_opening_no_longer_exported() -> None:
    with pytest.raises(ImportError):
        from sidequest.genre.models.narrative import MpOpening  # noqa: F401


def test_opening_is_exported() -> None:
    from sidequest.genre.models.narrative import Opening

    assert Opening is not None
