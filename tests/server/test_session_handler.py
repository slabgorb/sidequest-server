"""Test suite for session_handler.py — integration and wiring verification."""


def test_turn_complete_watcher_payload_omits_classified_intent():
    """Group A Task 6 — classified_intent fully retired from watcher stream."""
    import inspect
    from sidequest.server import session_handler

    source = inspect.getsource(session_handler)
    assert '"classified_intent"' not in source and "'classified_intent'" not in source, (
        "classified_intent string key still present in session_handler — "
        "Task 6 not complete"
    )
