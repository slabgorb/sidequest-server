"""Wire-first boundary tests for Story 45-6: chargen archetype-resolution gate.

Playtest 3 (2026-04-19, evropi) shipped a character (``pumblestone_sweedlewit``)
with ``resolved_archetype=NULL``: the snapshot looked valid, the persist
span fired, the state flipped to ``Playing`` — but the narrator had no
archetype anchor, character voice drifted, and the GM panel showed a
class with no archetype to back it.

Three silent-skip branches in
``WebSocketSessionHandler._resolve_character_archetype``
(``websocket_session_handler.py:546-628``) let chargen complete without
binding an archetype:

1. Builder produced no axis pair (``raw is None or "/" not in raw``).
2. Pack lacks ``base_archetypes`` / ``archetype_constraints``.
3. Resolver raised ``GenreValidationError`` (caught and swallowed).

Story 45-6 wires a gate at the chargen-confirmation seam (after
``_resolve_character_archetype``, before ``apply_starting_loadout``)
that distinguishes:

- ``OK_RESOLVED`` — pass (resolver wrote a display name).
- ``OK_NO_AXES`` — pass (pack opted out of the archetype system).
- ``BLOCKED_PARTIAL`` — fail with a typed ERROR frame
  ``code="chargen_archetype_unresolved"``.

Two OTEL spans wrap the gate so Sebastien's GM panel sees every
chargen-confirm decision:

- ``chargen.archetype_gate_evaluated`` — fires on every confirm with a
  ``state`` attribute (``"ok_resolved"`` | ``"ok_no_axes"`` |
  ``"blocked_partial"``).
- ``chargen.archetype_gate_blocked`` — fires only on the blocked branch
  with a ``block_reason`` attribute (``"raw_pair_unresolved"`` |
  ``"missing_axes_with_pack_axes"`` | ``"resolver_raised"``).

Wire-first discipline: tests drive the WS dispatch layer
(``handler.handle_message(CharacterCreationMessage(phase="confirmation"))``)
end-to-end. The gate must be reachable from the production seam, not a
unit-tested helper bolted on the side.

Boundary contract: a regression that removes the gate, the error code,
or either span will trip at least one of these tests.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from opentelemetry import trace as otel_trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from sidequest.game.builder import AccumulatedChoices, CharacterBuilder
from sidequest.genre.error import GenreValidationError
from sidequest.protocol.messages import (
    CharacterCreationMessage,
    CharacterCreationPayload,
    ErrorMessage,
    SessionEventMessage,
    SessionEventPayload,
)
from sidequest.server.session_handler import WebSocketSessionHandler, _State

CONTENT_ROOT = Path(__file__).resolve().parents[3] / "sidequest-content" / "genre_packs"


from tests.server.conftest import (  # noqa: E402
    mock_claude_client_factory as _mock_claude_client_factory,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def save_dir(tmp_path: Path) -> Path:
    """Per-test save directory so persist assertions are isolated."""
    return tmp_path


@pytest.fixture
def handler_factory(save_dir: Path):
    """Build a WebSocketSessionHandler bound to caverns_and_claudes content.

    caverns_and_claudes is the chosen substrate because:
    - It has ``base_archetypes`` (loaded from
      ``sidequest-content/archetypes_base.yaml``) and
      ``archetype_constraints`` (loaded from
      ``caverns_and_claudes/archetype_constraints.yaml``) — i.e. it
      declares axes, so the BLOCKED_PARTIAL case (pack-axes-set,
      hints-unset) is reachable.
    - As of Story 45-6 the pack's ``char_creation.yaml`` scene 1 sets
      ``jungian_hint=hero`` / ``rpg_role_hint=jack_of_all_trades`` (a
      ``common`` pairing). A default-1 walk now produces a
      fully-resolved archetype, so the OK_RESOLVED-on-default tests
      need no hint injection. The BLOCKED_PARTIAL and OK_NO_AXES tests
      explicitly null the hints via ``_inject_hints(..., None, None)``
      to recreate the pumblestone failure case.
    """
    if not (CONTENT_ROOT / "caverns_and_claudes").is_dir():
        pytest.skip("caverns_and_claudes content not found")

    def make() -> WebSocketSessionHandler:
        return WebSocketSessionHandler(
            claude_client_factory=_mock_claude_client_factory(),
            genre_pack_search_paths=[CONTENT_ROOT],
            save_dir=save_dir,
        )

    return make


@pytest.fixture
def otel_capture():
    """Install an in-memory OTEL exporter and yield it for assertions."""
    from sidequest.telemetry.setup import init_tracer

    init_tracer()
    provider = otel_trace.get_tracer_provider()
    assert isinstance(provider, TracerProvider)
    exporter = InMemorySpanExporter()
    processor = SimpleSpanProcessor(exporter)
    provider.add_span_processor(processor)
    try:
        yield exporter
    finally:
        processor.shutdown()


# ---------------------------------------------------------------------------
# WS-driven helpers
# ---------------------------------------------------------------------------


async def _connect(
    handler: WebSocketSessionHandler,
    *,
    player_name: str = "Pumblestone",
    world: str = "grimvault",
) -> SessionEventMessage:
    """Send SESSION_EVENT.connect and confirm the handler entered Creating."""
    payload = SessionEventPayload(
        event="connect",
        player_name=player_name,
        genre="caverns_and_claudes",
        world=world,
    )
    out = await handler.handle_message(SessionEventMessage(payload=payload, player_id=""))
    assert isinstance(out[0], SessionEventMessage)
    return out[0]


async def _walk_to_confirmation(handler: WebSocketSessionHandler) -> None:
    """Walk chargen scenes via CHARACTER_CREATION until the builder is at
    ``is_confirmation()``. Does NOT send the confirmation message — the
    test drives that itself so each AC can configure its pre-condition.
    """
    sd = handler._session_data  # type: ignore[attr-defined]
    builder = sd.builder
    assert builder is not None, "connect must construct a chargen builder"

    while not builder.is_confirmation():
        scene = builder.current_scene()
        if scene.choices:
            payload = CharacterCreationPayload(phase="scene", choice="1")
        elif scene.allows_freeform:
            payload = CharacterCreationPayload(phase="scene", choice="Pumblestone")
        else:
            payload = CharacterCreationPayload(phase="continue")
        out = await handler.handle_message(
            CharacterCreationMessage(payload=payload, player_id="pid")
        )
        if out and isinstance(out[0], ErrorMessage):
            raise AssertionError(f"walk error: {out[0].payload.message}")


async def _send_confirmation(handler: WebSocketSessionHandler) -> list:
    """Send the phase=confirmation message — the load-bearing one."""
    tracer = otel_trace.get_tracer("test")
    with tracer.start_as_current_span("chargen_confirmation"):
        return await handler.handle_message(
            CharacterCreationMessage(
                payload=CharacterCreationPayload(phase="confirmation"),
                player_id="pid",
            )
        )


def _spans_named(exporter: InMemorySpanExporter, name: str) -> list:
    """Return all finished spans with the given name."""
    return [s for s in exporter.get_finished_spans() if s.name == name]


def _inject_hints(
    monkeypatch: pytest.MonkeyPatch,
    *,
    jungian: str | None,
    rpg_role: str | None,
) -> None:
    """Override ``CharacterBuilder.accumulated`` to force the given hint
    pair onto the builder's accumulated state.

    The accumulator is recomputed from scene results on every call, so a
    direct attribute mutation wouldn't survive ``builder.build()``'s next
    ``self.accumulated()`` call. Wrapping the method is the durable seam.
    """
    real = CharacterBuilder.accumulated

    def fake(self: CharacterBuilder) -> AccumulatedChoices:
        acc = real(self)
        acc.jungian_hint = jungian
        acc.rpg_role_hint = rpg_role
        return acc

    monkeypatch.setattr(CharacterBuilder, "accumulated", fake)


# ---------------------------------------------------------------------------
# AC1 — OK_RESOLVED: axes-set pack + valid hints succeed
# ---------------------------------------------------------------------------


class TestArchetypeGateOkResolved:
    """AC1: a pack with ``base_archetypes`` and ``archetype_constraints``,
    chargen scenes that set both hints (``hero`` / ``tank`` is a
    ``common`` pairing in caverns_and_claudes), drives confirmation
    cleanly. Character is persisted, state flips to Playing,
    ``resolved_archetype`` is the resolved display name (NOT the raw
    ``"hero/tank"`` form)."""

    def test_resolved_archetype_is_display_name_not_raw_pair(
        self,
        handler_factory,
        monkeypatch: pytest.MonkeyPatch,
        otel_capture: InMemorySpanExporter,
    ) -> None:
        async def body() -> None:
            handler = handler_factory()
            await _connect(handler)
            await _walk_to_confirmation(handler)

            # ``hero`` / ``tank`` is a ``common`` pairing in
            # caverns_and_claudes/archetype_constraints.yaml. The resolver
            # must produce a display name (not the raw ``"hero/tank"``).
            _inject_hints(monkeypatch, jungian="hero", rpg_role="tank")

            out = await _send_confirmation(handler)
            assert out, "confirmation must produce at least one frame"
            # No ERROR frame — the gate must pass.
            for msg in out:
                assert not isinstance(msg, ErrorMessage), (
                    f"OK_RESOLVED branch should not error; got {msg!r}"
                )

            sd = handler._session_data  # type: ignore[attr-defined]
            assert sd.snapshot.characters, (
                "OK_RESOLVED must persist the character to the snapshot"
            )
            character = sd.snapshot.characters[0]
            assert character.resolved_archetype is not None, (
                "OK_RESOLVED must leave a non-None resolved_archetype"
            )
            assert "/" not in character.resolved_archetype, (
                "OK_RESOLVED must replace the raw 'j/r' pair with a "
                "display name; got: "
                f"{character.resolved_archetype!r}"
            )
            # The handler must transition to Playing on success.
            assert handler._state == _State.Playing  # type: ignore[attr-defined]

            # The gate evaluator span MUST fire even on the success
            # branch — that's the negative-confirmation Sebastien needs
            # to know the gate ran (not just absent because the path
            # bypassed it). Without this assertion, the test passes
            # today with NO gate present (the existing resolver already
            # produces the right state) — which would be vacuous.
            evaluated = _spans_named(
                otel_capture, "chargen.archetype_gate_evaluated"
            )
            assert evaluated, (
                "chargen.archetype_gate_evaluated must fire on the "
                "OK_RESOLVED branch — without this span, the test "
                "would pass even if the gate did not exist"
            )

        asyncio.run(body())


# ---------------------------------------------------------------------------
# AC2 — BLOCKED_PARTIAL: axes-set pack + missing hints fails
#
# This is the ``pumblestone`` regression: pack declares axes, chargen
# scene didn't set both hints, the silent-skip branch shipped a null-
# archetype character. The gate must block, the typed-ERROR frame must
# carry the documented code, the character must NOT be persisted, and
# the handler state must stay Creating.
# ---------------------------------------------------------------------------


class TestArchetypeGateBlockedPartial:
    """AC2: the ``pumblestone`` negative-to-positive regression.

    caverns_and_claudes declares axes (base_archetypes +
    archetype_constraints) but its chargen scenes set neither
    ``jungian_hint`` nor ``rpg_role_hint``. A default-1 walk produces a
    character with ``resolved_archetype=None``. The gate must reject
    confirmation."""

    def test_confirmation_returns_typed_error_with_documented_code(
        self,
        handler_factory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def body() -> None:
            handler = handler_factory()
            await _connect(handler)
            await _walk_to_confirmation(handler)

            # Strip both hints to simulate the pumblestone path
            # (caverns_and_claudes scene 1 sets default hints, so we
            # null them out to recreate the malformed-scene case the
            # gate is supposed to catch).
            _inject_hints(monkeypatch, jungian=None, rpg_role=None)

            out = await _send_confirmation(handler)
            assert out, "confirmation must produce a frame"
            assert isinstance(out[0], ErrorMessage), (
                "BLOCKED_PARTIAL must return an ERROR frame, got "
                f"{out[0]!r}"
            )
            err = out[0].payload
            assert err.code == "chargen_archetype_unresolved", (
                "BLOCKED_PARTIAL ERROR must carry the documented code "
                "'chargen_archetype_unresolved' so the UI can branch "
                f"without keyword-matching; got code={err.code!r}"
            )
            # Human-readable message must exist (NonBlankString
            # constructor enforces non-blank — we just confirm the field
            # is populated and unwraps to a non-empty string).
            assert err.message is not None, (
                "BLOCKED_PARTIAL must include a message field"
            )
            assert str(err.message).strip(), (
                "BLOCKED_PARTIAL message must unwrap to a non-empty "
                "string"
            )

        asyncio.run(body())

    def test_blocked_chargen_does_not_persist_character(
        self,
        handler_factory,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def body() -> None:
            handler = handler_factory()
            await _connect(handler)
            await _walk_to_confirmation(handler)

            # Recreate the pumblestone case: pack has axes, hints unset.
            _inject_hints(monkeypatch, jungian=None, rpg_role=None)

            sd_before = handler._session_data  # type: ignore[attr-defined]
            assert not sd_before.snapshot.characters, (
                "Pre-confirmation snapshot must have no characters"
            )

            await _send_confirmation(handler)

            sd_after = handler._session_data  # type: ignore[attr-defined]
            assert not sd_after.snapshot.characters, (
                "BLOCKED_PARTIAL must NOT append the character to "
                "sd.snapshot.characters — pumblestone shipped because "
                "the silent-skip path persisted anyway"
            )
            # Handler state must not flip — chargen is not done.
            assert handler._state == _State.Creating, (  # type: ignore[attr-defined]
                "BLOCKED_PARTIAL must keep the handler in Creating; got "
                f"{handler._state!r}"
            )

        asyncio.run(body())


# ---------------------------------------------------------------------------
# AC3 — OK_NO_AXES: pack with no axes succeeds with resolved_archetype=None
# ---------------------------------------------------------------------------


class TestArchetypeGateOkNoAxes:
    """AC3: a pack that opts out of the archetype system
    (``base_archetypes is None and archetype_constraints is None``)
    must chargen cleanly even when the character has
    ``resolved_archetype=None``. The gate distinguishes "pack opted out"
    from "pack opted in but scene malformed"."""

    def test_axisless_pack_chargen_succeeds_with_null_archetype(
        self,
        handler_factory,
        monkeypatch: pytest.MonkeyPatch,
        otel_capture: InMemorySpanExporter,
    ) -> None:
        async def body() -> None:
            handler = handler_factory()
            await _connect(handler)
            await _walk_to_confirmation(handler)

            # Strip axes from the loaded pack AND null out hints —
            # simulating a pack that legitimately doesn't use the
            # archetype system. Without nulling the hints, the
            # caverns_and_claudes default chargen scenes set them
            # (post-Story 45-6 content fix), the resolver runs against
            # the now-None pack axes via the early-return at
            # ``websocket_session_handler.py:577``, leaves the raw "j/r"
            # pair on the character, and the gate would correctly block
            # it as ``raw_pair_unresolved``.
            sd = handler._session_data  # type: ignore[attr-defined]
            sd.genre_pack.base_archetypes = None
            sd.genre_pack.archetype_constraints = None
            _inject_hints(monkeypatch, jungian=None, rpg_role=None)

            out = await _send_confirmation(handler)
            assert out, "confirmation must produce a frame"
            for msg in out:
                assert not isinstance(msg, ErrorMessage), (
                    "OK_NO_AXES must not error; pack opted out — "
                    f"got {msg!r}"
                )

            assert sd.snapshot.characters, (
                "OK_NO_AXES must persist the character"
            )
            assert sd.snapshot.characters[0].resolved_archetype is None, (
                "OK_NO_AXES is allowed to leave resolved_archetype=None"
            )
            assert handler._state == _State.Playing  # type: ignore[attr-defined]

            # The gate evaluator span MUST fire on the OK_NO_AXES
            # branch too — the lie-detector confirms the gate ran and
            # chose the pack-opted-out branch deliberately, vs. the
            # silent skip that shipped pumblestone. Without this
            # assertion the test passes today with no gate present.
            evaluated = _spans_named(
                otel_capture, "chargen.archetype_gate_evaluated"
            )
            assert evaluated, (
                "chargen.archetype_gate_evaluated must fire on the "
                "OK_NO_AXES branch — without this span the test would "
                "pass even if the gate did not exist"
            )

        asyncio.run(body())


# ---------------------------------------------------------------------------
# AC4 — OTEL: chargen.archetype_gate_evaluated fires every confirm with the
# right state attribute; chargen.archetype_gate_blocked fires only on
# blocked branch.
# ---------------------------------------------------------------------------


class TestArchetypeGateOtel:
    """AC4: the gate emits two OTEL spans so Sebastien's GM panel can
    see every chargen-confirm decision (Sebastien's lie-detector per
    CLAUDE.md OTEL Observability Principle)."""

    def test_evaluated_span_fires_on_ok_resolved_with_state_attr(
        self,
        handler_factory,
        monkeypatch: pytest.MonkeyPatch,
        otel_capture: InMemorySpanExporter,
    ) -> None:
        async def body() -> None:
            handler = handler_factory()
            await _connect(handler)
            await _walk_to_confirmation(handler)
            _inject_hints(monkeypatch, jungian="hero", rpg_role="tank")
            await _send_confirmation(handler)

            evaluated = _spans_named(
                otel_capture, "chargen.archetype_gate_evaluated"
            )
            assert evaluated, (
                "chargen.archetype_gate_evaluated must fire on every "
                "confirm — Sebastien's GM panel needs the negative "
                "confirmation that the gate ran"
            )
            attrs = dict(evaluated[-1].attributes or {})
            assert attrs.get("state") == "ok_resolved", (
                f"OK_RESOLVED span state must be 'ok_resolved'; got "
                f"{attrs.get('state')!r}"
            )
            # No blocked span on the success path.
            blocked = _spans_named(
                otel_capture, "chargen.archetype_gate_blocked"
            )
            assert not blocked, (
                "chargen.archetype_gate_blocked must NOT fire on the "
                "OK_RESOLVED branch"
            )

        asyncio.run(body())

    def test_evaluated_span_fires_on_ok_no_axes_with_state_attr(
        self,
        handler_factory,
        monkeypatch: pytest.MonkeyPatch,
        otel_capture: InMemorySpanExporter,
    ) -> None:
        async def body() -> None:
            handler = handler_factory()
            await _connect(handler)
            await _walk_to_confirmation(handler)

            sd = handler._session_data  # type: ignore[attr-defined]
            sd.genre_pack.base_archetypes = None
            sd.genre_pack.archetype_constraints = None
            # See test_axisless_pack_chargen_succeeds_with_null_archetype
            # for the rationale — pack-axisless + caverns default hints
            # would land in the gate's raw_pair_unresolved branch.
            _inject_hints(monkeypatch, jungian=None, rpg_role=None)

            await _send_confirmation(handler)

            evaluated = _spans_named(
                otel_capture, "chargen.archetype_gate_evaluated"
            )
            assert evaluated, (
                "chargen.archetype_gate_evaluated must fire on the "
                "OK_NO_AXES branch — every confirm path emits"
            )
            attrs = dict(evaluated[-1].attributes or {})
            assert attrs.get("state") == "ok_no_axes", (
                f"OK_NO_AXES span state must be 'ok_no_axes'; got "
                f"{attrs.get('state')!r}"
            )
            blocked = _spans_named(
                otel_capture, "chargen.archetype_gate_blocked"
            )
            assert not blocked, (
                "chargen.archetype_gate_blocked must NOT fire on the "
                "OK_NO_AXES branch"
            )

        asyncio.run(body())

    def test_blocked_span_fires_on_blocked_partial_with_block_reason(
        self,
        handler_factory,
        monkeypatch: pytest.MonkeyPatch,
        otel_capture: InMemorySpanExporter,
    ) -> None:
        async def body() -> None:
            handler = handler_factory()
            await _connect(handler)
            await _walk_to_confirmation(handler)

            # Recreate pumblestone: pack has axes, hints unset.
            _inject_hints(monkeypatch, jungian=None, rpg_role=None)

            await _send_confirmation(handler)

            evaluated = _spans_named(
                otel_capture, "chargen.archetype_gate_evaluated"
            )
            assert evaluated, (
                "chargen.archetype_gate_evaluated must fire on the "
                "BLOCKED_PARTIAL branch too — Sebastien needs the "
                "evaluator span every time"
            )
            ev_attrs = dict(evaluated[-1].attributes or {})
            assert ev_attrs.get("state") == "blocked_partial", (
                f"BLOCKED_PARTIAL evaluator span state must be "
                f"'blocked_partial'; got {ev_attrs.get('state')!r}"
            )

            blocked = _spans_named(
                otel_capture, "chargen.archetype_gate_blocked"
            )
            assert blocked, (
                "chargen.archetype_gate_blocked is the explicit "
                "lie-detector entry — it must fire when chargen would "
                "have shipped broken"
            )
            bl_attrs = dict(blocked[-1].attributes or {})
            # In the AC2 setup, hints are unset and pack has axes →
            # block_reason is missing_axes_with_pack_axes (chargen scene
            # malformed).
            assert bl_attrs.get("block_reason") == "missing_axes_with_pack_axes", (
                f"BLOCKED_PARTIAL with hints=None and pack-axes-set must "
                f"have block_reason='missing_axes_with_pack_axes'; got "
                f"{bl_attrs.get('block_reason')!r}"
            )

        asyncio.run(body())

    def test_span_routes_register_both_gate_spans(self) -> None:
        """SPAN_ROUTES registration is what makes the GM panel see these
        spans — without a route entry, the dashboard's typed tabs miss
        the new subsystem (per ``test_routing_completeness.py`` the
        constant must be in SPAN_ROUTES *or* FLAT_ONLY_SPANS, but for
        Sebastien's lie-detector the ``state_transition`` route is
        load-bearing)."""
        from sidequest.telemetry.spans import FLAT_ONLY_SPANS, SPAN_ROUTES

        for name in (
            "chargen.archetype_gate_evaluated",
            "chargen.archetype_gate_blocked",
        ):
            assert name in SPAN_ROUTES, (
                f"{name!r} must be registered in SPAN_ROUTES so the GM "
                f"panel emits a typed event when the span closes"
            )
            assert name not in FLAT_ONLY_SPANS, (
                f"{name!r} must NOT be in FLAT_ONLY_SPANS — the GM "
                f"panel needs the typed event, not just agent_span_close"
            )
            route = SPAN_ROUTES[name]
            # Per story context, both spans drive a state_transition
            # event tagged to a chargen / character_creation component.
            assert route.event_type == "state_transition", (
                f"{name} route must emit state_transition events; got "
                f"{route.event_type!r}"
            )


# ---------------------------------------------------------------------------
# AC5 — Resolver-raised path routes through the gate as BLOCKED_PARTIAL
# with block_reason="resolver_raised"
# ---------------------------------------------------------------------------


class TestArchetypeGateResolverRaised:
    """AC5: when the resolver raises ``GenreValidationError`` (forbidden
    pairing, unknown axis), the legacy ``archetype_resolution_failed``
    event still fires (no regression there) but the new gate observes
    that the raw pair is still on the character and blocks with
    ``block_reason="resolver_raised"``. The character is NOT
    persisted."""

    def test_resolver_raise_blocks_and_does_not_persist(
        self,
        handler_factory,
        monkeypatch: pytest.MonkeyPatch,
        otel_capture: InMemorySpanExporter,
    ) -> None:
        async def body() -> None:
            handler = handler_factory()
            await _connect(handler)
            await _walk_to_confirmation(handler)

            # Inject both hints so builder.build() produces a raw "j/r"
            # pair on the character — required for the resolver to be
            # called at all.
            _inject_hints(monkeypatch, jungian="hero", rpg_role="tank")

            # Stub the resolver to raise. This is the third silent-skip
            # branch (websocket_session_handler.py:593-610): the catch
            # logs and emits archetype_resolution_failed, then returns
            # — the raw pair stays on the character.
            def _raise(*args, **kwargs):
                raise GenreValidationError(
                    message="test-forced resolver raise"
                )

            monkeypatch.setattr(
                "sidequest.server.websocket_session_handler.resolve_archetype",
                _raise,
            )

            out = await _send_confirmation(handler)
            assert out and isinstance(out[0], ErrorMessage), (
                "Resolver-raised path must return a typed ERROR frame "
                "via the gate, not silently ship a partial character"
            )
            assert out[0].payload.code == "chargen_archetype_unresolved", (
                "Resolver-raised ERROR must carry the documented code"
            )

            sd = handler._session_data  # type: ignore[attr-defined]
            assert not sd.snapshot.characters, (
                "Resolver-raised path must not persist the character"
            )

            # Legacy resolver-failed event still fires (no regression).
            legacy_events = [
                e
                for span in otel_capture.get_finished_spans()
                for e in span.events
                if e.name == "character_creation.archetype_resolution_failed"
            ]
            assert legacy_events, (
                "The pre-existing archetype_resolution_failed event "
                "must still fire — gate wraps the resolver, doesn't "
                "replace it"
            )

            # New blocked span fires with block_reason='resolver_raised'.
            blocked = _spans_named(
                otel_capture, "chargen.archetype_gate_blocked"
            )
            assert blocked, (
                "chargen.archetype_gate_blocked must fire on the "
                "resolver-raised branch"
            )
            bl_attrs = dict(blocked[-1].attributes or {})
            assert bl_attrs.get("block_reason") == "resolver_raised", (
                "Resolver-raised gate-blocked span must distinguish "
                "this branch via block_reason='resolver_raised' so "
                "Sebastien can see WHY the gate blocked; got "
                f"{bl_attrs.get('block_reason')!r}"
            )

        asyncio.run(body())


# ---------------------------------------------------------------------------
# Wire test — verify the gate is actually called from production code
#
# Wire-first principle (CLAUDE.md): "Verify it's actually connected
# end-to-end. Tests passing and files existing means nothing if the
# component isn't imported, the hook isn't called, or the endpoint isn't
# hit in production code." — i.e. the gate helper, if extracted, must
# have a non-test consumer.
# ---------------------------------------------------------------------------


class TestArchetypeGateWiring:
    def test_gate_helper_has_production_consumer(self) -> None:
        """The gate's symbol — whatever Dev names the helper — MUST be
        called from ``_chargen_confirmation``. We assert via source-
        scan: at least one of the documented gate names is referenced
        from ``websocket_session_handler.py`` outside its own
        definition. If Dev extracts the gate but forgets to wire it,
        every test above might still pass (because the gate logic
        could be inlined elsewhere) but production would silently
        bypass — this catches that."""
        src = (
            Path(__file__).resolve().parents[2]
            / "sidequest"
            / "server"
            / "websocket_session_handler.py"
        ).read_text(encoding="utf-8")
        # The story context names ``_gate_archetype_resolution`` as the
        # helper. Accept that name OR an inline ``archetype_gate``
        # marker so Dev has flexibility — what matters is that the
        # production seam references the gate.
        candidates = [
            "_gate_archetype_resolution",
            "archetype_gate_evaluated",  # span name, ties inline gate to test
        ]
        assert any(token in src for token in candidates), (
            "The chargen-confirmation seam must reference the archetype "
            "gate by name (either the extracted helper "
            "'_gate_archetype_resolution' or the OTEL span "
            "'chargen.archetype_gate_evaluated') so a future refactor "
            "that drops the gate fails this wire-check. Source scan of "
            "websocket_session_handler.py found none of: "
            f"{candidates!r}"
        )
