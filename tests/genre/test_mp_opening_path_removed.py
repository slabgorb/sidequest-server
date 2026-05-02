"""Verify the legacy mp_opening.yaml parsing path is gone."""

from __future__ import annotations

import inspect

from sidequest.genre import loader


def test_mp_openings_no_longer_in_loader() -> None:
    """The variable name `mp_openings_raw` should no longer appear in loader source."""
    source = inspect.getsource(loader)
    assert "mp_openings_raw" not in source, (
        "Loader still references mp_openings_raw — the legacy "
        "mp_opening.yaml parsing path was not fully deleted."
    )
    assert "mp_opening.yaml" not in source, "Loader still references mp_opening.yaml as a path."
