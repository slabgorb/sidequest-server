"""Tests for sidequest.game.world_save.

Hub-world persistence — survives SqliteStore.init_session() reinit.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from sidequest.game.world_save import Hireling


def test_hireling_defaults_active_zero_stress():
    h = Hireling(id="vol_1", name="Volga Stein", archetype="prig")
    assert h.stress == 0
    assert h.status == "active"
    assert h.recruited_at_delve == 0
    assert h.notes == ""


def test_hireling_status_validates_literal():
    with pytest.raises(ValidationError):
        Hireling(id="vol_1", name="x", archetype="x", status="ghost")  # type: ignore[arg-type]


def test_hireling_id_pattern_enforced():
    """Item 4a's recruit generator and items 5/6/7's narrator-zone
    consumers share this contract — locked at model boundary."""
    # Valid shapes
    Hireling(id="vol_1", name="x", archetype="x")
    Hireling(id="prig_a3f", name="x", archetype="x")
    # Invalid shapes — must fail loud, no silent normalization
    for bad in ("Vol_1", "1vol", "vol-1", "vol 1", "", "vol!"):
        with pytest.raises(ValidationError):
            Hireling(id=bad, name="x", archetype="x")
