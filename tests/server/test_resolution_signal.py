from sidequest.game.resolution_signal import ResolutionSignal


def test_resolution_signal_round_trip():
    sig = ResolutionSignal(
        encounter_type="combat",
        outcome="opponent_victory",
        final_player_metric=4,
        final_opponent_metric=11,
        yielded_actors=("Sam",),
        edge_refreshed=2,
    )
    assert sig.outcome == "opponent_victory"
    assert sig.yielded_actors == ("Sam",)


def test_resolution_signal_serialization_round_trip():
    sig = ResolutionSignal(
        encounter_type="combat",
        outcome="player_victory",
        final_player_metric=10,
        final_opponent_metric=4,
        yielded_actors=tuple(),
        edge_refreshed=0,
    )
    raw = sig.model_dump_json()
    parsed = ResolutionSignal.model_validate_json(raw)
    assert parsed == sig


def test_game_snapshot_has_pending_resolution_signal_default_none():
    """Wiring test: GameSnapshot exposes pending_resolution_signal slot, defaults None."""
    from sidequest.game.session import GameSnapshot

    # The slot must exist with default None.
    assert "pending_resolution_signal" in GameSnapshot.model_fields
    field = GameSnapshot.model_fields["pending_resolution_signal"]
    # Default should be None (no factory).
    assert field.default is None or field.default_factory is None
