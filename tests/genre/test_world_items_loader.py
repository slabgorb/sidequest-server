"""World-tier items.yaml loader.

The loader surfaces five item sections per world (``named_items``,
``modifier_items``, ``reliquaries``, ``crimson_remnants``,
``consumable_items``) to the rest of the engine so the narrator and
downstream subsystems (Cleric ``divine_favor`` wiring) can read items
without parsing yaml themselves.

These tests pin five behaviors:

1. Missing ``items.yaml`` returns ``None`` (distinguishes "no file" from
   "empty sections").
2. All five sections round-trip through the catalog, preserving extra
   fields the narrator needs (``divine_favor_effect``, ``cost_signature``,
   ``confrontation_modifiers``).
3. Duplicate item ids across sections fail loud.
4. Malformed yaml fails loud as ``GenreLoadError``.
5. A successful load emits a ``state_transition`` watcher event with
   per-section counts so the GM panel can prove the subsystem engaged.

Plus a wiring smoke against the shipped ``caverns_sunden`` world — the
file the playgroup actually plays against — that confirms
``World.items`` is reachable via ``load_genre_pack``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from sidequest.genre.error import GenreLoadError
from sidequest.genre.loader import _load_world_items, load_genre_pack
from sidequest.genre.models.items import WorldItem, WorldItemsCatalog

CONTENT_ROOT = Path(__file__).resolve().parents[3] / "sidequest-content"
CC_PACK_DIR = CONTENT_ROOT / "genre_packs" / "caverns_and_claudes"
SUNDEN_WORLD_DIR = CC_PACK_DIR / "worlds" / "caverns_sunden"


# ---------------------------------------------------------------------------
# Watcher-event capture (mirror tests/genre/test_loader_cache_otel_wiring.py)
# ---------------------------------------------------------------------------


@pytest.fixture
def captured_events(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[dict[str, Any]]]:
    captured: list[dict[str, Any]] = []

    def _capture(event_type, fields, *, component="sidequest-server", severity="info"):
        captured.append(
            {
                "event_type": event_type,
                "fields": fields,
                "component": component,
                "severity": severity,
            }
        )

    from sidequest.telemetry import watcher_hub as hub_mod

    monkeypatch.setattr(hub_mod, "publish_event", _capture)
    yield captured


def _items_loaded_events(events: list[dict]) -> list[dict]:
    return [
        e
        for e in events
        if e["component"] == "genre"
        and e["event_type"] == "state_transition"
        and e["fields"].get("field") == "world_items"
        and e["fields"].get("op") == "loaded"
    ]


# ---------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------


FULL_ITEMS_YAML = """\
world: test_world
genre: caverns_and_claudes

named_items:
  - id: the_quiet_bell
    name: "The Quiet Bell"
    subtype: tool
    ocean: { o: 0.45, c: 0.85, e: 0.15, a: 0.55, n: 0.35 }
    nature: "Prefers silence."

modifier_items:
  - id: inga_tally_stick
    name: "A Pre-Counted Tally Stick"
    category: utility
    confrontation_modifiers:
      - confrontation_type: chase
        beat_id: horden_threshold_toll
        effect: satisfies_requires
        consumable: true

reliquaries:
  - id: confessional_alms_bowl
    name: "Anselm Vail's Confessional Alms-Bowl"
    subtype: relic
    divine_register: true
    keyed_to_rite: confessional
    opposes_sin: greed
    divine_favor_effect: |
      At divine_favor >= 0.7, the Cleric may extend the bowl.

crimson_remnants:
  - id: brand_of_wrzaz_tysul
    name: "The Brand of Wrząz-Tysul"
    crimson_god: "Wrząz-Tysul"
    cost_signature: flesh
    notoriety_acceleration: 2x

consumable_items:
  - id: confessional_scroll_of_lamplight
    name: "Confessional Scroll of Lamplight"
    category: magic
    rarity: rare
    replaces_baseline: scroll_light
"""


# ---------------------------------------------------------------------------
# Model-level
# ---------------------------------------------------------------------------


def test_world_item_requires_id_and_name() -> None:
    """Tolerant shape: id + name are the only required fields. Section-
    specific bag is accepted as extra and stays addressable on the model."""
    item = WorldItem.model_validate({"id": "x", "name": "X", "divine_favor_effect": "fires at 0.7"})
    assert item.id == "x"
    assert item.name == "X"
    dumped = item.model_dump()
    assert dumped["divine_favor_effect"] == "fires at 0.7"


def test_world_items_catalog_empty_defaults() -> None:
    catalog = WorldItemsCatalog()
    assert catalog.named_items == []
    assert catalog.reliquaries == []
    assert catalog.all_items() == []
    counts = catalog.section_counts()
    assert counts == {
        "named_items": 0,
        "modifier_items": 0,
        "reliquaries": 0,
        "crimson_remnants": 0,
        "consumable_items": 0,
    }


# ---------------------------------------------------------------------------
# Loader behavior
# ---------------------------------------------------------------------------


def test_missing_items_yaml_returns_none(tmp_path: Path, captured_events: list[dict]) -> None:
    """No file → None, no event. Distinguishes "world has no items"
    from "world has an empty items.yaml"."""
    result = _load_world_items(tmp_path / "items.yaml", world_slug="ghost_world")
    assert result is None
    assert _items_loaded_events(captured_events) == []


def test_full_five_section_load(tmp_path: Path, captured_events: list[dict]) -> None:
    """All five sections round-trip with their section-specific extras
    intact so the narrator and Cleric can read them. The loader emits a
    state_transition event with per-section counts."""
    items_path = tmp_path / "items.yaml"
    items_path.write_text(FULL_ITEMS_YAML, encoding="utf-8")

    catalog = _load_world_items(items_path, world_slug="test_world")
    assert catalog is not None

    # Section presence and counts.
    assert catalog.world == "test_world"
    assert len(catalog.named_items) == 1
    assert len(catalog.modifier_items) == 1
    assert len(catalog.reliquaries) == 1
    assert len(catalog.crimson_remnants) == 1
    assert len(catalog.consumable_items) == 1
    assert len(catalog.all_items()) == 5

    # Extra fields survive — Cleric narration reads divine_favor_effect
    # directly off the reliquary entry.
    reliquary = catalog.reliquaries[0]
    assert reliquary.id == "confessional_alms_bowl"
    relq_dump = reliquary.model_dump()
    assert relq_dump["keyed_to_rite"] == "confessional"
    assert "divine_favor >= 0.7" in relq_dump["divine_favor_effect"]

    # Crimson cost_signature also preserved.
    crimson_dump = catalog.crimson_remnants[0].model_dump()
    assert crimson_dump["cost_signature"] == "flesh"

    # OTEL: one loaded event with correct payload shape.
    events = _items_loaded_events(captured_events)
    assert len(events) == 1
    fields = events[0]["fields"]
    assert fields["world_slug"] == "test_world"
    assert fields["item_count"] == 5
    assert fields["named_items"] == 1
    assert fields["reliquaries"] == 1
    assert fields["crimson_remnants"] == 1
    assert fields["consumable_items"] == 1
    assert fields["modifier_items"] == 1
    assert fields["source"] == str(items_path)


def test_duplicate_id_across_sections_fails_loud(
    tmp_path: Path, captured_events: list[dict]
) -> None:
    """Ids address items in narrator context + game state. A collision
    between, e.g., a reliquary and a consumable would silently shadow one
    of them — surface it as a load error."""
    items_path = tmp_path / "items.yaml"
    items_path.write_text(
        "named_items:\n"
        "  - id: clash\n"
        "    name: Clash One\n"
        "reliquaries:\n"
        "  - id: clash\n"
        "    name: Clash Two\n",
        encoding="utf-8",
    )

    with pytest.raises(GenreLoadError) as exc_info:
        _load_world_items(items_path, world_slug="dup_world")
    assert "duplicate item id 'clash'" in str(exc_info.value)
    assert "named_items" in str(exc_info.value)
    assert "reliquaries" in str(exc_info.value)

    # No success event when load fails.
    assert _items_loaded_events(captured_events) == []


def test_malformed_yaml_fails_loud(tmp_path: Path) -> None:
    items_path = tmp_path / "items.yaml"
    items_path.write_text("named_items:\n  - id: x\n  name: missing-dash\n", encoding="utf-8")
    with pytest.raises(GenreLoadError):
        _load_world_items(items_path, world_slug="bad_world")


def test_item_missing_required_field_fails_loud(tmp_path: Path) -> None:
    """No silent acceptance of items missing id — they couldn't be
    addressed afterward."""
    items_path = tmp_path / "items.yaml"
    items_path.write_text("named_items:\n  - name: Nameless One\n", encoding="utf-8")
    with pytest.raises(GenreLoadError):
        _load_world_items(items_path, world_slug="bad_world")


# ---------------------------------------------------------------------------
# Wiring: items reachable through the full pack loader
# ---------------------------------------------------------------------------


def test_caverns_sunden_world_exposes_items_via_load_genre_pack() -> None:
    """End-to-end wiring smoke. ``load_genre_pack`` must populate
    ``World.items`` for caverns_sunden (the playgroup's world). At least
    one ``named_items`` entry is required because the shipped file
    authors them — this is the integration test for the wiring chain
    described in CLAUDE.md's 'Every Test Suite Needs a Wiring Test'.

    The world's items.yaml currently ships at least named_items + modifier_items
    (PR #211 adds the other three sections). The wiring assertion is
    deliberately tolerant of the section mix — only the presence of items
    on World.items matters."""
    if not (SUNDEN_WORLD_DIR / "items.yaml").exists():
        pytest.skip("caverns_sunden world is missing items.yaml on this checkout")

    pack = load_genre_pack(CC_PACK_DIR)
    world = pack.worlds.get("caverns_sunden")
    assert world is not None, "caverns_sunden world missing from pack"
    assert world.items is not None, "World.items must be populated when items.yaml exists"
    assert len(world.items.all_items()) > 0, (
        "caverns_sunden ships at least named_items + modifier_items; "
        "an empty catalog means the loader silently dropped them."
    )

    # Spot-check: every loaded item has both id and name (the loader's
    # only hard constraint).
    for item in world.items.all_items():
        assert item.id, "every loaded item must have an id"
        assert item.name, "every loaded item must have a name"
