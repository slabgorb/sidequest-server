"""Beneath Sünden Plan 5 — persistence layer tests.

Round-trip, freeze, no-floor, overlay, ledger, OTEL, and the Plan-7
wiring contract. Real SQLite only (:memory: + temp-file for WAL).
"""

from __future__ import annotations


def test_persistence_module_importable() -> None:
    import sidequest.dungeon.persistence as persistence

    assert hasattr(persistence, "DungeonStore")
