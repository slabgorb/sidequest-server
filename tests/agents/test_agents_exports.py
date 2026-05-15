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


def test_anthropic_sdk_client_exported() -> None:
    from sidequest.agents import AnthropicSdkClient

    assert AnthropicSdkClient is not None


def test_tooling_protocol_exports() -> None:
    from sidequest.agents import (
        CacheableBlock,
        Message,
        ToolDefinition,
        ToolingLlmClient,
        ToolingResult,
        ToolResultBlock,
        ToolUseBlock,
    )

    assert all(
        x is not None
        for x in (
            CacheableBlock,
            Message,
            ToolDefinition,
            ToolingLlmClient,
            ToolingResult,
            ToolResultBlock,
            ToolUseBlock,
        )
    )


def test_call_type_and_resolver_exported() -> None:
    from sidequest.agents import CallType, resolve_model

    assert resolve_model(CallType.NARRATION) == "claude-sonnet-4-6"


def test_phase_b_registry_exports() -> None:
    from sidequest.agents import (
        NoopPerceptionFilter,
        PerceptionFilter,
        Registry,
        ToolCategory,
        ToolContext,
        ToolResult,
        ToolResultStatus,
        default_registry,
        tool,
    )

    assert Registry is not None
    assert tool is not None
    assert default_registry is not None
    assert PerceptionFilter is not None
    assert NoopPerceptionFilter is not None
    assert ToolCategory is not None
    assert ToolContext is not None
    assert ToolResult is not None
    assert ToolResultStatus is not None
