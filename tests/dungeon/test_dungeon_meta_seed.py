from __future__ import annotations

import sqlite3

import pytest

from sidequest.dungeon.persistence import DungeonStore, PersistError


def _mem() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


def test_campaign_seed_absent_on_fresh_save() -> None:
    ds = DungeonStore(_mem())
    ds.ensure_schema()
    assert ds.get_campaign_seed() is None


def test_campaign_seed_roundtrips_verbatim() -> None:
    conn = _mem()
    ds = DungeonStore(conn)
    ds.ensure_schema()
    ds.set_campaign_seed(4611686018427387903)  # a 63-bit value
    conn.commit()
    assert ds.get_campaign_seed() == 4611686018427387903


def test_campaign_seed_is_write_once() -> None:
    conn = _mem()
    ds = DungeonStore(conn)
    ds.ensure_schema()
    ds.set_campaign_seed(111)
    conn.commit()
    with pytest.raises(PersistError):
        ds.set_campaign_seed(222)
    assert ds.get_campaign_seed() == 111  # frozen — refused overwrite
