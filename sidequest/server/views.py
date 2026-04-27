"""View projection helpers extracted from WebSocketSessionHandler.

Phase 2 of the session_handler.py decomposition (see
docs/superpowers/specs/2026-04-27-session-handler-decomposition-design.md).

Each function takes ``handler: WebSocketSessionHandler`` as its first
argument (or operates on read-only inputs in the case of
``is_hidden_status_list``). No new abstractions introduced — this is pure
extraction with byte-identical behavior to the original methods on
WebSocketSessionHandler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sidequest.game.status import Status

if TYPE_CHECKING:
    from sidequest.game.character import Character
    from sidequest.game.projection.view import SessionGameStateView
    from sidequest.protocol.messages import PartyStatusMessage
    from sidequest.server.session_handler import WebSocketSessionHandler, _SessionData


_HIDDEN_STATUS_TOKENS: frozenset[str] = frozenset(
    {
        "hidden",
        "invisible",
        "stealth",
        "concealed",
    }
)


def is_hidden_status_list(statuses: list[Status]) -> bool:
    """Return True iff any status's lowercased text matches a hidden-marker
    token (whole-token membership, not substring)."""
    return any(s.text.lower() in _HIDDEN_STATUS_TOKENS for s in statuses)


def build_game_state_view(handler: WebSocketSessionHandler) -> SessionGameStateView:
    """Read-only view of current session state for the projection filter.

    Zone + visibility state is populated from the live ``GameSnapshot``:
    all player-characters share the party-level ``snapshot.location``,
    and NPCs report their per-entity ``Npc.location``. Creatures whose
    ``statuses`` contain a stealth-like marker go into
    ``hidden_characters`` so ``visible_to()`` masks them even when
    co-located with the viewer. Per-item ownership is not yet tracked
    and stays at the conservative default.

    **GM identity wiring (C1, still partial):**

    - Solo sessions have no separate GM player by design; ``gm_player_id``
      is correctly ``None`` there. ``CoreInvariantStage`` never
      short-circuits on ``is_gm()`` for solo — which is the right
      behavior, because in solo play the single player is the only
      recipient and has no counterpart to be "GM" to.
    - Multiplayer sessions *should* name a GM player (e.g. the session
      creator or a designated seat) so that ``unless: is_gm()`` in
      ``projection.yaml`` can exempt them. That wiring lives downstream
      of MP-02 seating — ``SessionRoom`` does not yet carry a GM seat
      designation, so we still fall through to ``None`` for multiplayer
      with a logged warning. Genre packs that ship ``unless: is_gm()``
      rules today will mask the GM identically to a regular player
      (the safe direction: over-redact rather than leak).

    **Player-character mapping:** ``Character`` does not yet carry a
    ``player_id`` attribute, so the session's active player_id
    (``sd.player_id``) is mapped to the first entry in
    ``snapshot.characters`` — the single-player case this branch is
    authoritative for today. MP seat-assignment (sprint 2) will feed
    the multi-player case via ``SessionRoom``. When no character
    exists yet (pre-chargen) the mapping stays empty and predicates
    that depend on ``character_of()`` evaluate to ``False`` (the
    masked direction).
    """
    from sidequest.game.persistence import GameMode  # noqa: PLC0415 — break import cycle
    from sidequest.game.projection.view import SessionGameStateView
    from sidequest.server.session_handler import logger

    sd = handler._session_data
    if sd is None:
        return SessionGameStateView(gm_player_id=None, player_id_to_character={})

    # Solo: no human GM. None is correct; CoreInvariantStage's
    # gm-sees-all branch never fires for the single player.
    gm_player_id: str | None = None
    if sd.mode is not None and sd.mode != GameMode.SOLO:
        # Multiplayer: GM seat assignment not yet plumbed through
        # SessionRoom. Log one warning per build so GM-panel users
        # can see that ``unless: is_gm()`` rules are currently
        # over-masking the GM in multiplayer sessions.
        if not getattr(handler, "_gm_wiring_warned", False):
            logger.warning(
                "projection.gm_identity_unwired slug=%s mode=%s — "
                "multiplayer sessions do not yet carry a GM-seat "
                "designation; `unless: is_gm()` rules will mask the "
                "GM like any other player until MP-02 GM seating "
                "lands.",
                sd.game_slug,
                sd.mode,
            )
            handler._gm_wiring_warned = True

    snapshot = sd.snapshot

    # Player -> Character.name mapping. Solo / single-player sessions
    # today have exactly one character; that character belongs to the
    # session's active player_id. Without this mapping, the predicate
    # path (e.g. ``visible_to(target)``) receives
    # ``view.character_of(player_id) is None`` and short-circuits to
    # False before ever consulting zone data. Populated from the
    # existing session state — no new fields introduced.
    mapping: dict[str, str] = {}
    if snapshot.characters:
        mapping[sd.player_id] = snapshot.characters[0].core.name

    # Zone + hidden-character tracking from the live snapshot. Characters
    # share the party-level location today (no per-character zone split
    # in the engine yet); NPCs carry their own ``location``. Keys are
    # creature names — the same identity the rest of the projection
    # system uses when it refers to characters by ID. Single pass per
    # collection so character_zones and hidden_characters stay in sync.
    character_zones: dict[str, str] = {}
    hidden_characters: set[str] = set()
    party_zone = snapshot.location or None

    # One-shot OTEL breadcrumb: if we have player-characters but no
    # party zone, every co-located visible_to() collapses to False.
    # The direction is conservative-correct but invisible to the GM
    # panel — surface it once per session so rule authors can see why
    # their ``visible_to`` rules are masking everything.
    if (
        party_zone is None
        and snapshot.characters
        and not getattr(handler, "_party_zone_absent_warned", False)
    ):
        logger.warning(
            "projection.party_zone_absent_with_characters slug=%s "
            "characters=%d — snapshot.location is empty while "
            "snapshot.characters is non-empty; visible_to() / "
            "in_same_zone() will mask every co-located target until "
            "a location is set (typically the first encounter).",
            sd.game_slug,
            len(snapshot.characters),
        )
        handler._party_zone_absent_warned = True

    for ch in snapshot.characters:
        name = ch.core.name
        if party_zone is not None:
            character_zones[name] = party_zone
        if is_hidden_status_list(ch.core.statuses):
            hidden_characters.add(name)
    for npc in snapshot.npcs:
        name = npc.core.name
        if npc.location:
            character_zones[name] = npc.location
        if is_hidden_status_list(npc.core.statuses):
            hidden_characters.add(name)

    return SessionGameStateView(
        gm_player_id=gm_player_id,
        player_id_to_character=mapping,
        character_zones=character_zones,
        hidden_characters=hidden_characters,
    )
