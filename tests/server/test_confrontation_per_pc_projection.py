"""Per-PC beat projection on CONFRONTATION payload (story 49-7, RED).

Found in the 2026-05-12 Carl/Donut/Katia caverns_sunden playtest. During
the Chalk Moth combat the right-rail Confrontation tab on every connected
tab showed the FULL 16-button union of every class's beats — Carl
(Fighter) saw Backstab/Slip Behind (Thief), Cast Cantrip/Cast Spell
(Mage), Turn Undead/Pray for Aid (Cleric); Donut and Katia saw the
same identical list.

These tests exercise the new contract on
``sidequest.server.dispatch.confrontation.build_confrontation_payload``:
an optional ``recipient_pc`` keyword carrying ``(class_def, spell_slots,
prepared_spells)``. When supplied the payload's ``beats`` field is the
result of ``beats_available_for(cdef, class_def, slots, prepared)`` so
the UI overlay only renders class-legal choices. When ``recipient_pc``
is omitted the payload keeps the pre-fix shape (full union) so the
narrator-prompt builder, slug-resume bootstrap, and any test/caller
that hasn't migrated yet continue to work.

The shape of the recipient context — a 3-tuple ``(ClassDef, float,
dict[int, list[str]] | None)`` — mirrors the existing
``pc_classes_by_name`` convention in
``sidequest.agents.narrator.NarratorAgent.register_state_for_turn`` so
the two call sites use one shape for one decision.
"""

from __future__ import annotations

from sidequest.game.beat_filter import beats_available_for
from sidequest.game.encounter import (
    EncounterActor,
    EncounterMetric,
    EncounterPhase,
    StructuredEncounter,
)
from sidequest.genre.models.character import ClassDef
from sidequest.genre.models.rules import (
    BeatDef,
    BeatKind,
    ConfrontationDef,
    MetricDef,
)
from sidequest.server.dispatch.confrontation import build_confrontation_payload


def _beat(
    id_: str,
    *,
    class_filter: list[str] | None = None,
    kind: BeatKind = BeatKind.strike,
    stat: str = "STR",
) -> BeatDef:
    return BeatDef(
        id=id_,
        label=id_.replace("_", " ").title(),
        kind=kind,
        stat_check=stat,
        class_filter=class_filter,
    )


def _cdef_playtest_union() -> ConfrontationDef:
    """The 16-button union that leaked in the 2026-05-12 playtest, in
    miniature: universal beats + per-class specials so each class's
    filtered slice is distinguishable."""
    return ConfrontationDef(
        type="combat",
        label="Dungeon Combat",
        category="combat",
        player_metric=MetricDef(name="momentum", starting=0, threshold=10),
        opponent_metric=MetricDef(name="momentum", starting=0, threshold=10),
        beats=[
            # Universal-ish (no class_filter) — every class can pick these.
            _beat("attack"),
            _beat("defend", stat="CON"),
            _beat("flee", stat="DEX"),
            # Fighter-only
            _beat("shield_bash", class_filter=["Fighter"]),
            # Thief-only
            _beat("backstab", class_filter=["Thief"], stat="DEX"),
            _beat("slip_behind", class_filter=["Thief"], stat="DEX"),
            # Mage-only
            _beat("cast_cantrip", class_filter=["Mage"], stat="INT"),
            _beat("cast_spell", class_filter=["Mage"], stat="INT"),
            # Cleric-only
            _beat("turn_undead", class_filter=["Cleric"], stat="WIS"),
            _beat("pray_for_aid", class_filter=["Cleric"], stat="CHA"),
        ],
    )


def _class(name: str, choices: list[str]) -> ClassDef:
    return ClassDef(
        id=name.lower(),
        display_name=name,
        rpg_role="tank",
        jungian_default="warrior",
        prime_requisite="STR",
        minimum_score=9,
        kit_table=f"{name.lower()}_kit",
        flavor="-",
        encounter_beat_choices=choices,
    )


def _encounter(actor_name: str = "Carl") -> StructuredEncounter:
    return StructuredEncounter(
        encounter_type="combat",
        player_metric=EncounterMetric(name="momentum", current=0, starting=0, threshold=10),
        opponent_metric=EncounterMetric(name="momentum", current=0, starting=0, threshold=10),
        structured_phase=EncounterPhase.Setup,
        actors=[EncounterActor(name=actor_name, role="combatant", side="player")],
    )


FIGHTER_CHOICES = ["attack", "defend", "flee", "shield_bash"]
THIEF_CHOICES = ["attack", "defend", "flee", "backstab", "slip_behind"]
MAGE_CHOICES = ["attack", "defend", "flee", "cast_cantrip", "cast_spell"]
CLERIC_CHOICES = ["attack", "defend", "flee", "turn_undead", "pray_for_aid"]


# ---------------------------------------------------------------------------
# Backward-compat: omitting recipient_pc preserves the pre-fix shape.
# ---------------------------------------------------------------------------


def test_recipient_pc_none_returns_full_beats_list_backward_compat() -> None:
    """Pre-fix callers that don't pass recipient_pc keep receiving the
    full union. This protects narrator.py's prompt-rendering call (which
    intentionally shows the all-beats list to the LLM for opponent
    selection) and the existing test_confrontation_dispatch.py suite.
    """
    cdef = _cdef_playtest_union()
    payload = build_confrontation_payload(
        encounter=_encounter(),
        cdef=cdef,
        genre_slug="caverns_and_claudes",
    )
    assert sorted(b["id"] for b in payload["beats"]) == sorted(b.id for b in cdef.beats)


# ---------------------------------------------------------------------------
# The playtest signature failure — class projection.
# ---------------------------------------------------------------------------


def test_fighter_recipient_excludes_thief_mage_cleric_beats() -> None:
    """Carl's tab in the playtest. Fighter must NOT see Backstab/Slip
    Behind (Thief), Cast Cantrip/Cast Spell (Mage), Turn Undead/Pray
    for Aid (Cleric).
    """
    cdef = _cdef_playtest_union()
    fighter = _class("Fighter", FIGHTER_CHOICES)
    payload = build_confrontation_payload(
        encounter=_encounter("Carl"),
        cdef=cdef,
        genre_slug="caverns_and_claudes",
        recipient_pc=(fighter, 0.0, None),
    )
    ids = {b["id"] for b in payload["beats"]}
    # The playtest leak set — must be ABSENT for a Fighter recipient.
    leaked_in_playtest = {
        "backstab",
        "slip_behind",
        "cast_cantrip",
        "cast_spell",
        "turn_undead",
        "pray_for_aid",
    }
    assert ids.isdisjoint(leaked_in_playtest), (
        f"Fighter recipient must not receive other-class beats; "
        f"received {sorted(ids & leaked_in_playtest)!r}"
    )
    # And Fighter's legal beats ARE present.
    assert {"attack", "defend", "shield_bash"} <= ids


def test_thief_recipient_includes_thief_specific_beats() -> None:
    cdef = _cdef_playtest_union()
    thief = _class("Thief", THIEF_CHOICES)
    payload = build_confrontation_payload(
        encounter=_encounter("Katia"),
        cdef=cdef,
        genre_slug="caverns_and_claudes",
        recipient_pc=(thief, 0.0, None),
    )
    ids = {b["id"] for b in payload["beats"]}
    assert {"backstab", "slip_behind"} <= ids
    # Other classes' specials must not leak.
    assert "shield_bash" not in ids
    assert "cast_spell" not in ids
    assert "turn_undead" not in ids


def test_cleric_recipient_includes_cleric_specials_and_excludes_cast_spell() -> None:
    """Donut's tab in the playtest. Cleric saw Mage's Cast Spell because
    no per-PC filter ran. Cleric is not in cast_spell's class_filter and
    cast_spell is not in cleric's encounter_beat_choices — the engine
    must reject it even when slots and prepared spells are present (the
    class gate is the strongest of the three independent gates in
    beats_available_for).
    """
    cdef = _cdef_playtest_union()
    cleric = _class("Cleric", CLERIC_CHOICES)
    payload = build_confrontation_payload(
        encounter=_encounter("Donut"),
        cdef=cdef,
        genre_slug="caverns_and_claudes",
        recipient_pc=(cleric, 2.0, {1: ["bless", "cure_light_wounds"]}),
    )
    ids = {b["id"] for b in payload["beats"]}
    assert {"turn_undead", "pray_for_aid"} <= ids
    assert "cast_spell" not in ids
    assert "cast_cantrip" not in ids


def test_mage_recipient_with_zero_slots_filters_cast_spell() -> None:
    """Slot gate: spell_slots_remaining < 1.0 → cast_spell unselectable
    even for a class that's in its filter."""
    cdef = _cdef_playtest_union()
    mage = _class("Mage", MAGE_CHOICES)
    payload = build_confrontation_payload(
        encounter=_encounter(),
        cdef=cdef,
        genre_slug="caverns_and_claudes",
        recipient_pc=(mage, 0.0, {1: ["sleep"]}),
    )
    ids = {b["id"] for b in payload["beats"]}
    assert "cast_cantrip" in ids
    assert "cast_spell" not in ids, (
        f"Mage with 0 slots must not see cast_spell; got beats={sorted(ids)!r}"
    )


def test_mage_recipient_with_slots_but_unprepared_filters_cast_spell() -> None:
    """Story 47-10 prepared-list gate. Slot > 0 but prepared_spells={}
    (nothing memorized) → cast_spell unselectable. This is the gate that
    correctly refused Donut's out-of-prep Sanctuary in the same playtest
    — it must continue to fire on the panel-projection path.
    """
    cdef = _cdef_playtest_union()
    mage = _class("Mage", MAGE_CHOICES)
    payload = build_confrontation_payload(
        encounter=_encounter(),
        cdef=cdef,
        genre_slug="caverns_and_claudes",
        recipient_pc=(mage, 2.0, {}),
    )
    ids = {b["id"] for b in payload["beats"]}
    assert "cast_spell" not in ids


def test_mage_recipient_with_slots_and_prepared_includes_cast_spell() -> None:
    """Slot > 0 and at least one spell prepared at any level → cast_spell
    is in the payload."""
    cdef = _cdef_playtest_union()
    mage = _class("Mage", MAGE_CHOICES)
    payload = build_confrontation_payload(
        encounter=_encounter(),
        cdef=cdef,
        genre_slug="caverns_and_claudes",
        recipient_pc=(mage, 2.0, {1: ["magic_missile"]}),
    )
    ids = {b["id"] for b in payload["beats"]}
    assert "cast_spell" in ids


def test_mage_recipient_with_only_higher_level_prepared_still_includes_cast_spell() -> None:
    """The prepared-list gate is 'something is prepared at SOME level'
    (matches beat_filter.py contract). Per-level slot routing is a
    follow-up — at this layer L2-only is enough to allow cast_spell.
    """
    cdef = _cdef_playtest_union()
    mage = _class("Mage", MAGE_CHOICES)
    payload = build_confrontation_payload(
        encounter=_encounter(),
        cdef=cdef,
        genre_slug="caverns_and_claudes",
        recipient_pc=(mage, 1.0, {2: ["fireball"]}),
    )
    ids = {b["id"] for b in payload["beats"]}
    assert "cast_spell" in ids


# ---------------------------------------------------------------------------
# Numerical contract — must match beats_available_for exactly.
# ---------------------------------------------------------------------------


def test_recipient_pc_size_and_ids_match_beats_available_for() -> None:
    """build_confrontation_payload(recipient_pc=...) is a thin wrapper
    over beats_available_for — the filtered list must match exactly in
    size and content. Single source of truth for the filter semantics
    (CLAUDE.md: 'Don't Reinvent — Wire Up What Exists').
    """
    cdef = _cdef_playtest_union()
    for class_name, choices in (
        ("Fighter", FIGHTER_CHOICES),
        ("Thief", THIEF_CHOICES),
        ("Mage", MAGE_CHOICES),
        ("Cleric", CLERIC_CHOICES),
    ):
        cls = _class(class_name, choices)
        payload = build_confrontation_payload(
            encounter=_encounter(),
            cdef=cdef,
            genre_slug="caverns_and_claudes",
            recipient_pc=(cls, 2.0, {1: ["spell"]}),
        )
        expected = beats_available_for(
            cdef,
            cls,
            spell_slots_remaining=2.0,
            prepared_spells={1: ["spell"]},
        )
        assert len(payload["beats"]) == len(expected), (
            f"{class_name}: payload size {len(payload['beats'])} != filter size "
            f"{len(expected)}; payload ids={[b['id'] for b in payload['beats']]!r}, "
            f"filter ids={[b.id for b in expected]!r}"
        )
        assert {b["id"] for b in payload["beats"]} == {b.id for b in expected}, (
            f"{class_name}: beat id sets differ between payload and filter"
        )


# ---------------------------------------------------------------------------
# Non-regression on the rest of the payload shape.
# ---------------------------------------------------------------------------


def test_recipient_pc_only_alters_beats_field() -> None:
    """The filter operates only on ``beats``. Everything else
    (type/label/category/actors/metrics/mood/active/genre_slug) must
    match the unfiltered payload byte-for-byte. A regression that
    accidentally rebuilds these fields differently per recipient would
    desynchronize the UI across tabs.
    """
    cdef = _cdef_playtest_union()
    enc = _encounter()
    fighter = _class("Fighter", FIGHTER_CHOICES)
    full = build_confrontation_payload(encounter=enc, cdef=cdef, genre_slug="caverns_and_claudes")
    filtered = build_confrontation_payload(
        encounter=enc,
        cdef=cdef,
        genre_slug="caverns_and_claudes",
        recipient_pc=(fighter, 0.0, None),
    )
    invariant_fields = (
        "type",
        "label",
        "category",
        "actors",
        "player_metric",
        "opponent_metric",
        "secondary_stats",
        "genre_slug",
        "mood",
        "active",
    )
    for key in invariant_fields:
        assert full[key] == filtered[key], (
            f"recipient_pc must not alter {key!r}: "
            f"unfiltered={full[key]!r} filtered={filtered[key]!r}"
        )


def test_recipient_pc_preserves_mood_override_precedence() -> None:
    """Encounter.mood_override still wins over cdef.mood when recipient_pc
    is supplied. Already tested in the unfiltered path; this is the
    regression guard for the filtered path."""
    cdef = _cdef_playtest_union().model_copy(update={"mood": "pack-mood"})
    enc = _encounter()
    enc.mood_override = "panic"
    fighter = _class("Fighter", FIGHTER_CHOICES)
    payload = build_confrontation_payload(
        encounter=enc,
        cdef=cdef,
        genre_slug="caverns_and_claudes",
        recipient_pc=(fighter, 0.0, None),
    )
    assert payload["mood"] == "panic"


# ---------------------------------------------------------------------------
# Pre-fix smoke — assertion that REPLICATES the bug, so the test fails
# both before and after the API exists but for distinct reasons.
# ---------------------------------------------------------------------------


def test_per_recipient_payloads_differ_for_fighter_vs_thief() -> None:
    """The playtest bug had Carl (Fighter) and Katia (Thief) seeing the
    identical 16-button list. After the fix the two recipients' payloads
    must differ — at minimum, Fighter sees shield_bash and not backstab,
    Thief sees backstab and not shield_bash.
    """
    cdef = _cdef_playtest_union()
    fighter = _class("Fighter", FIGHTER_CHOICES)
    thief = _class("Thief", THIEF_CHOICES)
    fighter_payload = build_confrontation_payload(
        encounter=_encounter("Carl"),
        cdef=cdef,
        genre_slug="caverns_and_claudes",
        recipient_pc=(fighter, 0.0, None),
    )
    thief_payload = build_confrontation_payload(
        encounter=_encounter("Katia"),
        cdef=cdef,
        genre_slug="caverns_and_claudes",
        recipient_pc=(thief, 0.0, None),
    )
    fighter_ids = {b["id"] for b in fighter_payload["beats"]}
    thief_ids = {b["id"] for b in thief_payload["beats"]}
    assert fighter_ids != thief_ids, (
        f"Fighter and Thief recipients must receive different beat sets; "
        f"both got {sorted(fighter_ids)!r} — this is the playtest bug."
    )
    assert "shield_bash" in fighter_ids and "shield_bash" not in thief_ids
    assert "backstab" in thief_ids and "backstab" not in fighter_ids
