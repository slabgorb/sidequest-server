"""RED tests — Story 47-6 AC4 — every silent return path emits OTEL.

``process_room_entry`` has multiple silent-return points with zero
watcher events. Per CLAUDE.md "OTEL Observability Principle," every
backend decision path must emit a span so the GM panel (Sebastien-mode
lie detector) can verify the hook engaged.

This story pins two new span constants and asserts they fire on every
relevant path:

* ``room.entry_skipped`` — fires when process_room_entry returns
  without evaluating eligibility. Required attributes:
  - ``reason``: one of ``"not_chassis_room"``, ``"no_bond_for_actor"``,
    ``"chassis_not_found"`` (``no_magic_state`` deferred to 47-7).
  - ``room_id``: the input room_id (never None)
  - ``actor_id``: the input character_id

* ``room.entry_evaluated`` — fires when eligibility is computed.
  Required attributes:
  - ``chassis_id``: the chassis evaluated against
  - ``room_local_id``: the resolved chassis-scoped room id
  - ``eligible_count``: int — confrontations passing fire_conditions
  - ``fired_count``: int — confrontations actually dispatched (may be
    less than eligible_count if cooldown blocks)

Both constants must be exported from ``sidequest.telemetry.spans.rig``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from sidequest.game.chassis import init_chassis_registry
from sidequest.game.session import GameSnapshot
from sidequest.genre.loader import load_genre_pack

REPO_ROOT = Path(__file__).resolve().parents[3]
SPACE_OPERA = REPO_ROOT / "sidequest-content" / "genre_packs" / "space_opera"


def _bootstrap_coyote_star_snapshot() -> GameSnapshot:
    if not SPACE_OPERA.exists():
        pytest.skip("space_opera content pack not present")
    pack = load_genre_pack(SPACE_OPERA)
    snap = GameSnapshot(
        genre_slug="space_opera",
        world_slug="coyote_star",
        location="Cockpit",
    )
    init_chassis_registry(snap, pack)
    return snap


def _span_attrs_by_name(otel_capture, name: str) -> list[dict]:
    """Return the attribute dicts for every span with the given name."""
    return [
        dict(s.attributes)
        for s in otel_capture.get_finished_spans()
        if s.name == name
    ]


# ---------------------------------------------------------------------------
# AC4: span constants are exported.
# ---------------------------------------------------------------------------


def test_room_entry_span_constants_are_exported() -> None:
    """Both new constants must be importable from telemetry.spans.rig."""
    from sidequest.telemetry.spans.rig import (
        SPAN_ROOM_ENTRY_EVALUATED,
        SPAN_ROOM_ENTRY_SKIPPED,
    )

    assert SPAN_ROOM_ENTRY_SKIPPED == "room.entry_skipped"
    assert SPAN_ROOM_ENTRY_EVALUATED == "room.entry_evaluated"


# ---------------------------------------------------------------------------
# AC4: skipped paths emit the span with the right reason.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_skipped_span_emitted_when_room_not_in_any_chassis(
    otel_capture,
) -> None:
    """A bare world-name room with no matching chassis interior_room must
    emit ``room.entry_skipped`` with reason=not_chassis_room — not silent."""
    from sidequest.game.room_movement import process_room_entry
    from sidequest.telemetry.spans.rig import SPAN_ROOM_ENTRY_SKIPPED

    snap = _bootstrap_coyote_star_snapshot()
    process_room_entry(
        snap,
        character_id="player_character",
        room_id="Docking Ring Office",
        current_turn=10,
    )

    attrs_list = _span_attrs_by_name(otel_capture, SPAN_ROOM_ENTRY_SKIPPED)
    assert attrs_list, (
        "room.entry_skipped span not emitted on non-chassis room input"
    )
    assert any(
        a.get("reason") == "not_chassis_room" for a in attrs_list
    ), f"expected reason=not_chassis_room; saw {attrs_list}"


@pytest.mark.integration
def test_skipped_span_emitted_when_no_bond_for_actor(otel_capture) -> None:
    """Galley + valid chassis but actor has no bond_ledger entry → must
    emit room.entry_skipped reason=no_bond_for_actor (not silent return)."""
    from sidequest.game.room_movement import process_room_entry
    from sidequest.telemetry.spans.rig import SPAN_ROOM_ENTRY_SKIPPED

    snap = _bootstrap_coyote_star_snapshot()
    # Use a name that has NO bond_ledger entry — placeholder is
    # "player_character"; "Stranger" is unknown.
    process_room_entry(
        snap,
        character_id="Stranger McNobody",
        room_id="The Kestrel — Galley",
        current_turn=10,
    )

    attrs_list = _span_attrs_by_name(otel_capture, SPAN_ROOM_ENTRY_SKIPPED)
    assert attrs_list, (
        "room.entry_skipped span not emitted when actor has no bond entry"
    )
    assert any(
        a.get("reason") == "no_bond_for_actor" for a in attrs_list
    ), f"expected reason=no_bond_for_actor; saw {attrs_list}"


# Deferred: ``no_magic_state`` skip reason. On the current branch
# (origin/main pre-45-43), ``process_room_entry`` reads from
# ``snapshot.world_confrontations`` rather than ``magic_state``. The
# defensive check is premature — it belongs to story 47-7 (magic bars
# init at world-load) where ``magic_state`` becomes load-bearing.
# Keeping the constant in the rig spans module so 47-7 can wire it
# cheaply without re-pinning the contract.


# ---------------------------------------------------------------------------
# AC4: evaluated path emits span with eligible/fired counts.
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_evaluated_span_emitted_on_galley_entry(otel_capture) -> None:
    """Galley entry with eligible bond → room.entry_evaluated span with
    chassis_id, room_local_id, eligible_count >= 1, fired_count >= 1."""
    from sidequest.game.room_movement import process_room_entry
    from sidequest.telemetry.spans.rig import SPAN_ROOM_ENTRY_EVALUATED

    snap = _bootstrap_coyote_star_snapshot()
    process_room_entry(
        snap,
        character_id="player_character",
        room_id="The Kestrel — Galley",
        current_turn=10,
    )

    attrs_list = _span_attrs_by_name(otel_capture, SPAN_ROOM_ENTRY_EVALUATED)
    assert attrs_list, (
        "room.entry_evaluated span not emitted on a successful galley entry"
    )
    attrs = attrs_list[-1]
    assert attrs.get("chassis_id") == "kestrel", attrs
    assert attrs.get("room_local_id") == "galley", attrs
    assert attrs.get("eligible_count", 0) >= 1, attrs
    assert attrs.get("fired_count", 0) >= 1, attrs


@pytest.mark.integration
def test_evaluated_span_emitted_with_fired_zero_when_cooldown_blocks(
    otel_capture,
) -> None:
    """Re-entry within cooldown: eligible_count includes the matched
    confrontation, but fired_count must be 0 because cooldown gates
    dispatch. The span makes the gating visible to Sebastien."""
    from sidequest.game.room_movement import process_room_entry
    from sidequest.telemetry.spans.rig import SPAN_ROOM_ENTRY_EVALUATED

    snap = _bootstrap_coyote_star_snapshot()
    process_room_entry(
        snap,
        character_id="player_character",
        room_id="The Kestrel — Galley",
        current_turn=10,
    )
    # Re-enter 3 turns later; cooldown_turns=6 means this fires no outputs.
    process_room_entry(
        snap,
        character_id="player_character",
        room_id="The Kestrel — Galley",
        current_turn=13,
    )

    attrs_list = _span_attrs_by_name(otel_capture, SPAN_ROOM_ENTRY_EVALUATED)
    # Two evaluations: first fires, second is cooldown-blocked.
    assert len(attrs_list) >= 2, (
        f"expected ≥2 room.entry_evaluated spans; saw {len(attrs_list)}"
    )
    second = attrs_list[1]
    # Story 47-6 review finding (cross-confirmed by 3 reviewer subagents):
    # the span's stated purpose is to let the GM panel distinguish
    # "no confrontation matched" (eligible_count==0, fired_count==0)
    # from "matched but cooldown-gated" (eligible_count>=1, fired_count==0).
    # Asserting only fired_count==0 lets a broken implementation pass —
    # eligible_count==0 also satisfies it. Both fields must be checked.
    assert second.get("eligible_count", 0) >= 1, (
        f"second entry should still SEE the confrontation as a match — "
        f"the cooldown-blocking distinction relies on eligible_count "
        f"reflecting matched-before-cooldown candidates. saw {second}"
    )
    assert second.get("fired_count", -1) == 0, (
        f"second entry should be cooldown-blocked (fired_count==0); "
        f"saw {second}"
    )
