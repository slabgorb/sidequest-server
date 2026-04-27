"""Tests for the /dashboard route — relocates the OTEL dashboard from
``scripts/playtest_dashboard.py`` into the sidequest-server FastAPI app.
"""

from __future__ import annotations

from importlib.resources import files


def test_dashboard_html_ships_with_package() -> None:
    """The dashboard HTML must live inside the ``sidequest.server``
    package so it is included in the wheel. If this fails, the file
    was added under the wrong directory or the hatchling include
    config drifted.
    """
    asset = files("sidequest.server").joinpath("static/dashboard.html")
    assert asset.is_file(), f"dashboard.html missing from package: {asset}"
