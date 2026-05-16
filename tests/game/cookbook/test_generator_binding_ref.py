"""Spec §7: every looks[].generator_binding must be a known oq-1 binding."""

from __future__ import annotations

import pytest

from sidequest.game.cookbook.loader import (
    KNOWN_GENERATOR_BINDINGS,
    CookbookValidationError,
    validate_bundle,
)


def test_shipped_looks_use_known_bindings(bundle) -> None:
    for look in bundle.looks:
        assert look.generator_binding in KNOWN_GENERATOR_BINDINGS


def test_unknown_binding_is_loud(bundle) -> None:
    broken_look = bundle.looks[0].model_copy(update={"generator_binding": "nonexistent_gen"})
    broken = type(bundle)(
        monsters=bundle.monsters,
        items=bundle.items,
        register=bundle.register,
        races=bundle.races,
        looks=[broken_look, *bundle.looks[1:]],
        affinities=bundle.affinities,
        specials=bundle.specials,
    )
    with pytest.raises(CookbookValidationError, match="nonexistent_gen"):
        validate_bundle(broken)
