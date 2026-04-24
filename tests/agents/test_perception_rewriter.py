from sidequest.agents.perception_rewriter import rewrite_for_recipient


def test_blind_recipient_gets_no_visual_spans():
    payload = {
        "text": "A guard slumps into shadow.",
        "spans": [
            {"id": "s1", "kind": "visual_only", "text": "slumps into shadow"},
            {"id": "s2", "kind": "audio_only", "text": "A soft thud"},
        ],
        "_visibility": {"visible_to": "all", "fidelity": {"p1": "audio_only"}},
    }
    out = rewrite_for_recipient(
        canonical_payload=payload,
        viewer_player_id="p1",
        status_effects={"p1": ["blinded"]},
    )
    kinds = [s["kind"] for s in out["spans"]]
    assert "visual_only" not in kinds
    assert "audio_only" in kinds


def test_full_fidelity_no_change():
    payload = {
        "text": "A quiet evening.",
        "spans": [{"id": "s1", "kind": "full", "text": "A quiet evening."}],
        "_visibility": {"visible_to": "all", "fidelity": {}},
    }
    out = rewrite_for_recipient(
        canonical_payload=payload,
        viewer_player_id="p1",
        status_effects={"p1": []},
    )
    assert out == payload


def test_deafened_recipient_gets_no_audio_spans():
    payload = {
        "text": "A distant shout.",
        "spans": [
            {"id": "s1", "kind": "audio_only", "text": "a shout"},
            {"id": "s2", "kind": "visual_only", "text": "a figure in the window"},
        ],
        "_visibility": {"visible_to": "all", "fidelity": {}},
    }
    out = rewrite_for_recipient(
        canonical_payload=payload,
        viewer_player_id="p1",
        status_effects={"p1": ["deafened"]},
    )
    kinds = [s["kind"] for s in out["spans"]]
    assert "audio_only" not in kinds
    assert "visual_only" in kinds


def test_status_override_trumps_full_base_fidelity():
    """Even if base fidelity is full, blinded still strips visual spans."""
    payload = {
        "text": "X",
        "spans": [
            {"id": "s1", "kind": "visual_only", "text": "v"},
            {"id": "s2", "kind": "audio_only", "text": "a"},
        ],
        # No entry for p1 in fidelity => base=full
        "_visibility": {"visible_to": "all", "fidelity": {}},
    }
    out = rewrite_for_recipient(
        canonical_payload=payload,
        viewer_player_id="p1",
        status_effects={"p1": ["blinded"]},
    )
    kinds = [s["kind"] for s in out["spans"]]
    assert "visual_only" not in kinds
