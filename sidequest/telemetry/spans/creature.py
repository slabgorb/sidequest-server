"""Creature core spans — HP delta tracking."""

from __future__ import annotations

from ._core import FLAT_ONLY_SPANS

SPAN_CREATURE_HP_DELTA = "creature.hp_delta"

FLAT_ONLY_SPANS.add(SPAN_CREATURE_HP_DELTA)
