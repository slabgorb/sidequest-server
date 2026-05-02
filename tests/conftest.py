"""Shared pytest fixtures for sidequest-server tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-snapshots",
        action="store_true",
        default=False,
        help="Refresh recorded SVG snapshots in tests/orbital/snapshots/.",
    )


@pytest.fixture(scope="session")
def content_dir() -> Path:
    """Path to the sidequest-content repo (genre packs, worlds)."""
    return Path(__file__).resolve().parent.parent.parent / "sidequest-content"


@pytest.fixture
def tmp_save_dir(tmp_path: Path) -> Path:
    """Temporary save directory per test."""
    save_dir = tmp_path / "saves"
    save_dir.mkdir()
    return save_dir


@pytest.fixture
async def initialized_tracer() -> AsyncIterator[None]:
    """Initialize OTEL tracer for the duration of a test."""
    from sidequest.telemetry import init_tracer

    init_tracer(service_name="sidequest-server-test")
    yield
