"""RED-phase negative-space tests for Story 45-26.

Asserts the absence of the legacy ``/api/saves/*`` REST routes and the
``(genre, world, player)``-tuple ``db_path_for_session`` save-path
helper. Both are post-port residue from ADR-082; MP-03 unified saves on
a single ``game_slug`` so these are dead code.

Today these tests fail RED — the routes are still registered, the
helper is still defined, and ``session_handler`` and ``rest`` still
import the helper. They turn GREEN once Dev deletes the routes (rest.py
283–462), the helper (persistence.py 472–482), and the legacy
``_handle_connect`` branch in ``session_handler.py``.

Spec: ``sprint/context/context-story-45-26.md`` ACs 1–3.
"""
from __future__ import annotations

from pathlib import Path

from sidequest.server.app import create_app


def test_legacy_save_routes_are_not_registered(tmp_path: Path) -> None:
    """AC-1: ``/api/saves``, ``/api/saves/new``, and any
    ``/api/saves/{...}`` path must be absent from a freshly-created app.
    """
    saves_dir = tmp_path / "saves"
    saves_dir.mkdir()
    app = create_app(
        genre_pack_search_paths=[tmp_path],
        save_dir=saves_dir,
    )
    route_paths = [r.path for r in app.routes if hasattr(r, "path")]

    assert "/api/saves" not in route_paths, (
        f"Legacy GET /api/saves still registered. Routes: {route_paths}"
    )
    assert "/api/saves/new" not in route_paths, (
        f"Legacy POST /api/saves/new still registered. "
        f"Routes: {route_paths}"
    )
    legacy_dynamic = [p for p in route_paths if p.startswith("/api/saves/")]
    assert not legacy_dynamic, (
        f"Legacy DELETE /api/saves/{{...}} still registered. "
        f"Found: {legacy_dynamic}"
    )


def test_legacy_db_path_for_session_helper_removed() -> None:
    """AC-2: the ``(genre, world, player)``-tuple save-path helper must
    be gone from ``sidequest.game.persistence``. ``db_path_for_slug`` is
    the canonical post-MP-03 helper and must remain.
    """
    import sidequest.game.persistence as p

    assert not hasattr(p, "db_path_for_session"), (
        "db_path_for_session was scheduled for removal in 45-26 — "
        "use db_path_for_slug instead."
    )
    assert hasattr(p, "db_path_for_slug"), (
        "db_path_for_slug is the canonical save-path helper post-MP-03 "
        "and must not be removed by this story."
    )


def test_no_module_references_db_path_for_session() -> None:
    """AC-3: no source module under ``sidequest/`` may import or
    reference ``db_path_for_session``. This catches the residual import
    in ``rest.py`` and the legacy ``_handle_connect`` branch in
    ``session_handler.py`` that still calls the helper.

    Test files are excluded — those are deleted/retargeted as part of
    AC-4 (handled by the existing test-suite migration), not by this
    grep gate.
    """
    import sidequest

    pkg_root = Path(sidequest.__file__).parent
    offenders: list[str] = []
    for py_file in pkg_root.rglob("*.py"):
        text = py_file.read_text(encoding="utf-8")
        if "db_path_for_session" in text:
            offenders.append(str(py_file.relative_to(pkg_root)))
    assert not offenders, (
        "db_path_for_session is referenced in production code: "
        f"{offenders}. Story 45-26 requires zero call sites in "
        "sidequest/ — see context-story-45-26.md AC-3."
    )
