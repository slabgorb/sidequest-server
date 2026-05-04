"""S4 — session-level EncounterTag renamed to NpcEncounterLogTag.

The old name remains as a deprecated alias for one release window so
external save files and any unmigrated test fixtures keep round-tripping.
"""

from __future__ import annotations

import warnings


def test_npc_encounter_log_tag_importable_under_new_name() -> None:
    from sidequest.game.session import NpcEncounterLogTag

    tag = NpcEncounterLogTag(
        npc_id="captain_orin",
        encounter_type="dialogue",
        archetype_id=None,
        notes=None,
    )
    assert tag.npc_id == "captain_orin"
    assert tag.encounter_type == "dialogue"


def test_narrative_entry_uses_npc_encounter_log_tag() -> None:
    from sidequest.game.session import NarrativeEntry, NpcEncounterLogTag

    entry = NarrativeEntry(
        author="narrator",
        content="Orin nods.",
        encounter_tags=[
            NpcEncounterLogTag(npc_id="captain_orin", encounter_type="dialogue")
        ],
    )
    assert isinstance(entry.encounter_tags[0], NpcEncounterLogTag)


def test_old_name_alias_still_works() -> None:
    """Deprecation alias — drop in the release after this one."""
    from sidequest.game import EncounterTag as DeprecatedAlias
    from sidequest.game.session import NpcEncounterLogTag

    # The alias must resolve to the new class.
    assert DeprecatedAlias is NpcEncounterLogTag


def test_scene_momentum_encounter_tag_unchanged() -> None:
    """The OTHER EncounterTag (game/encounter_tag.py — leverage/target/fleeting)
    is unaffected. This test pins the distinction so a future rename doesn't
    silently merge the two types."""
    from sidequest.game.encounter_tag import EncounterTag as SceneMomentumTag

    tag = SceneMomentumTag(
        text="The floor is lava",
        created_by="narrator",
        target=None,
        leverage=2,
        fleeting=False,
        created_turn=5,
    )
    assert tag.text == "The floor is lava"
    assert tag.leverage == 2
