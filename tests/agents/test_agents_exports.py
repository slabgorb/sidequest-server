"""Test that agents package exports are correct — guards against accidental broken wiring."""


def test_preprocessor_module_is_removed():
    """Group A Task 5 — dormant preprocessor port deleted."""
    import importlib

    try:
        importlib.import_module("sidequest.agents.preprocessor")
    except ModuleNotFoundError:
        return
    raise AssertionError("sidequest.agents.preprocessor still importable — Task 5 not complete")


def test_preprocessor_exports_are_gone():
    """Group A Task 5 — agents package no longer re-exports preprocessor symbols."""
    from sidequest.agents import __all__

    for dead in [
        "preprocess_action",
        "preprocess_action_with_client",
        "PreprocessError",
        "LlmFailed",
        "ParseFailed",
        "OutputTooLong",
    ]:
        assert dead not in __all__, f"{dead} still exported from sidequest.agents"
