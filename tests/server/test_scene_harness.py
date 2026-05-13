"""RED tests for Story 50-18 — POST /dev/scene/{name} dev-gated endpoint.

Wire-level + watcher-level integration tests for the scene-harness HTTP
endpoint specified in ADR-092. The endpoint hydrates a fixture YAML into
a GameSnapshot, persists via SqliteStore, and returns the minted slug —
all gated behind ``DEV_SCENES=1`` so production builds carry zero surface.

Contract under test (ADR-092 §Decision):

1. ``POST /dev/scene/{name}`` is registered ONLY when ``DEV_SCENES=1`` is
   set when ``create_app()`` runs. With the env var unset, the route does
   not appear in ``app.routes`` and a POST to it returns 404 (FastAPI's
   default for an unmatched path).
2. On success, returns ``{"slug": "<game_slug>"}`` and the save file is
   present at ``db_path_for_slug(save_dir, slug)`` with the hydrated
   snapshot in ``game_state``.
3. Missing fixture → 404 with the missing path in the JSON body.
4. Hydration error → 422 with field-level detail.
5. OTEL: emits ``scene_harness.intent.load``, ``.hydrate.ok``, ``.persist.ok``
   spans on the success path; ``.hydrate.error`` on the failure path.
6. The route is wired in through the production ``create_app()`` factory,
   not via a hand-built test app — verifies real integration per CLAUDE.md
   "Every Test Suite Needs a Wiring Test".

All tests currently RED — the route, hydrator, and span helpers do not
exist yet (ADR-092 implementation-status: partial; ADR-087 P0).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parents[3]
CANONICAL_FIXTURES_DIR = REPO_ROOT / "scenarios" / "fixtures"


# ── Fixtures: capture watcher events ────────────────────────────────────────


def _capture_events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict, dict]]:
    """Patch ``publish_event`` on the watcher hub module so every emitted
    semantic span is captured for the assertion phase.

    Mirrors the pattern in ``tests/server/test_render_mounts.py``.
    """
    captured: list[tuple[str, dict, dict]] = []

    def fake_publish(
        event_type: str,
        fields: dict[str, Any],
        *,
        component: str = "",
        severity: str = "info",
    ) -> None:
        captured.append(
            (event_type, dict(fields), {"component": component, "severity": severity})
        )

    import sidequest.telemetry.watcher_hub as _hub

    monkeypatch.setattr(_hub, "publish_event", fake_publish)
    return captured


def _build_dev_scenes_app(
    monkeypatch: pytest.MonkeyPatch,
    *,
    save_dir: Path,
    fixtures_dir: Path = CANONICAL_FIXTURES_DIR,
):
    """Construct a production-shaped FastAPI app with ``DEV_SCENES=1`` set.

    Uses the real ``create_app()`` factory — not a hand-built ``FastAPI()``
    — because the wiring-test rule (CLAUDE.md) requires every test suite
    to verify the component is reachable through production code paths.
    """
    monkeypatch.setenv("DEV_SCENES", "1")
    monkeypatch.setenv("SIDEQUEST_FIXTURES_DIR", str(fixtures_dir))

    # Importing inside the helper keeps the env mutation in scope for the
    # one-shot factory call — ``create_app()`` reads the env at construction
    # time per ADR-092 §Decision point 1.
    from sidequest.server.app import create_app

    return create_app(
        save_dir=save_dir,
        genre_pack_search_paths=[],
    )


# ── AC-2: route absent without DEV_SCENES=1 ─────────────────────────────────


def test_scene_route_absent_when_dev_scenes_env_unset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """ADR-092 §Decision point 1: when ``DEV_SCENES`` is unset, the
    ``/dev/scene/{name}`` route MUST NOT be registered. POST returns 404
    (FastAPI's default for unmatched path) — production builds carry
    ZERO scene-harness surface."""
    monkeypatch.delenv("DEV_SCENES", raising=False)

    from sidequest.server.app import create_app

    app = create_app(save_dir=tmp_path, genre_pack_search_paths=[])

    # The route must not appear in ``app.routes`` at all — not present-but-403,
    # not present-but-redirected. Absent.
    paths = {getattr(r, "path", "") for r in app.routes}
    scene_routes = [p for p in paths if "/dev/scene" in p]
    assert scene_routes == [], (
        f"DEV_SCENES unset — /dev/scene/* must not be registered, found: {scene_routes!r}"
    )

    client = TestClient(app)
    r = client.post("/dev/scene/combat_test")
    assert r.status_code == 404, (
        f"DEV_SCENES unset — POST /dev/scene/combat_test must 404, got {r.status_code}"
    )


def test_scene_route_absent_when_dev_scenes_env_set_to_zero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Only the exact string ``"1"`` enables the route. ``"0"``, ``"false"``,
    ``""`` — all keep the production-safe default. This is a "fail-closed"
    test: ambiguity in the flag value must NOT silently enable dev surface."""
    monkeypatch.setenv("DEV_SCENES", "0")

    from sidequest.server.app import create_app

    app = create_app(save_dir=tmp_path, genre_pack_search_paths=[])

    client = TestClient(app)
    r = client.post("/dev/scene/combat_test")
    assert r.status_code == 404, (
        f"DEV_SCENES=0 must keep the route absent, got {r.status_code}"
    )


# ── AC-1: route present + happy path ────────────────────────────────────────


def test_scene_route_registered_when_dev_scenes_env_set(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """With ``DEV_SCENES=1`` and a real canonical fixture, the route is
    registered AND a POST succeeds. This is the wiring test."""
    app = _build_dev_scenes_app(monkeypatch, save_dir=tmp_path)

    paths = {getattr(r, "path", "") for r in app.routes}
    scene_routes = [p for p in paths if "/dev/scene" in p]
    assert scene_routes, (
        f"DEV_SCENES=1 — /dev/scene/* must be registered. routes: {sorted(paths)!r}"
    )

    client = TestClient(app)
    r = client.post("/dev/scene/combat_test")
    assert r.status_code == 200, (
        f"POST /dev/scene/combat_test (DEV_SCENES=1) must succeed; "
        f"got {r.status_code}, body: {r.text}"
    )


def test_scene_post_response_body_has_slug_field(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """ADR-092 §Decision point 1: response is ``{"slug": "<game_slug>"}``.

    The UI side (App.tsx:1402) destructures ``{ slug }`` — the field name is
    a contract, not an implementation detail."""
    app = _build_dev_scenes_app(monkeypatch, save_dir=tmp_path)
    client = TestClient(app)

    r = client.post("/dev/scene/combat_test")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict)
    assert "slug" in body, f"response missing 'slug' field: {body!r}"
    assert isinstance(body["slug"], str) and body["slug"], (
        f"slug must be a non-empty string, got {body['slug']!r}"
    )


def test_scene_post_persists_save_file_at_slug_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """ADR-092 §Decision point 2: persist via the existing SqliteStore.

    After a successful POST, the save file MUST exist at
    ``db_path_for_slug(save_dir, slug)`` — the same path the slug-keyed
    connect handler (dispatch_connect) will look at."""
    app = _build_dev_scenes_app(monkeypatch, save_dir=tmp_path)
    client = TestClient(app)

    r = client.post("/dev/scene/combat_test")
    assert r.status_code == 200
    slug = r.json()["slug"]

    from sidequest.game.persistence import db_path_for_slug

    expected = db_path_for_slug(tmp_path, slug)
    assert expected.exists(), (
        f"save file missing at expected slug path {expected!s}; "
        f"scene-harness must persist via SqliteStore so dispatch_connect can find it"
    )


def test_scene_post_persisted_snapshot_carries_fixture_genre_and_world(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The persisted snapshot must round-trip the fixture's identity fields.

    Loading the saved game_state and parsing it back into GameSnapshot
    yields ``genre_slug=mutant_wasteland`` and ``world_slug=flickering_reach``
    (from combat_test.yaml). If these are blank or wrong, every
    downstream genre-pack lookup will silently fall through."""
    app = _build_dev_scenes_app(monkeypatch, save_dir=tmp_path)
    client = TestClient(app)

    r = client.post("/dev/scene/combat_test")
    assert r.status_code == 200
    slug = r.json()["slug"]

    from sidequest.game.persistence import SqliteStore, db_path_for_slug

    store = SqliteStore(db_path_for_slug(tmp_path, slug))
    store.initialize()
    saved = store.load()
    assert saved is not None, "save file exists but SqliteStore.load returned None"

    # ``SavedSession.snapshot`` is the hydrated GameSnapshot.
    snapshot = saved.snapshot
    assert snapshot.genre_slug == "mutant_wasteland"
    assert snapshot.world_slug == "flickering_reach"


def test_scene_post_persisted_snapshot_carries_fixture_character(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """The fixture's ``character:`` block must reach ``snapshot.characters[0]``.

    Without this, the slug-connect handler sees ``has_character=False`` and
    drops the player back into chargen — defeating the entire point of
    the scene harness (skip chargen for iteration)."""
    app = _build_dev_scenes_app(monkeypatch, save_dir=tmp_path)
    client = TestClient(app)

    r = client.post("/dev/scene/combat_test")
    slug = r.json()["slug"]

    from sidequest.game.persistence import SqliteStore, db_path_for_slug

    store = SqliteStore(db_path_for_slug(tmp_path, slug))
    store.initialize()
    saved = store.load()
    assert saved is not None
    snapshot = saved.snapshot

    assert len(snapshot.characters) >= 1, (
        "combat_test.yaml has a character block — snapshot.characters[0] must be populated"
    )
    # Character nests CreatureCore under ``.core``; ``Character.name`` is a method.
    assert snapshot.characters[0].core.name == "Skar"


@pytest.mark.parametrize(
    "fixture_name",
    ["combat_test", "dogfight", "negotiation", "poker"],
)
def test_every_canonical_fixture_can_be_loaded_via_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    fixture_name: str,
) -> None:
    """ADR-092 §AC-4: all four canonical fixtures hydrate via the URL flow.

    The UI's ``?scene=NAME`` path will hit each of these; any single one
    failing breaks Keith's iteration loop for that mechanic."""
    app = _build_dev_scenes_app(monkeypatch, save_dir=tmp_path)
    client = TestClient(app)

    r = client.post(f"/dev/scene/{fixture_name}")
    assert r.status_code == 200, (
        f"canonical fixture {fixture_name!r} must hydrate via the endpoint; "
        f"got {r.status_code}, body: {r.text}"
    )


# ── AC-3: failure is loud ───────────────────────────────────────────────────


def test_unknown_fixture_returns_404_with_path_in_body(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """ADR-092 §Decision point 5: missing fixture YAML → 404 with the
    missing path in the body. Loud failure — never silent fall through."""
    app = _build_dev_scenes_app(monkeypatch, save_dir=tmp_path)
    client = TestClient(app)

    r = client.post("/dev/scene/does_not_exist_anywhere")
    assert r.status_code == 404
    body = r.json()
    # Body must surface the missing fixture name (and ideally the path) so
    # the dev knows which file to create — not a generic "not found".
    body_text = repr(body).lower()
    assert "does_not_exist_anywhere" in body_text, (
        f"404 body must name the missing fixture; got {body!r}"
    )


def test_malformed_fixture_yaml_returns_422_with_field_detail(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """ADR-092 §Decision point 5: hydration error → 422 with field-level
    detail. A fixture missing ``genre:`` must surface a 422 that says
    ``genre`` somewhere — not 500, not a stack trace."""
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    bad = fixtures_dir / "no_genre.yaml"
    bad.write_text("world: default\n", encoding="utf-8")

    save_dir = tmp_path / "saves"
    save_dir.mkdir()

    app = _build_dev_scenes_app(monkeypatch, save_dir=save_dir, fixtures_dir=fixtures_dir)
    client = TestClient(app)

    r = client.post("/dev/scene/no_genre")
    assert r.status_code == 422, (
        f"malformed fixture (missing genre) must surface 422, got {r.status_code}: {r.text}"
    )
    body_text = repr(r.json()).lower()
    assert "genre" in body_text, (
        f"422 body must surface the failing field name; got {r.json()!r}"
    )


# ── OTEL: watcher events on every subsystem decision (CLAUDE.md principle) ──


def test_scene_harness_emits_load_intent_span(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """ADR-092 §OTEL: emit ``scene_harness.intent.load`` with
    ``fixture_name`` and (ultimately) ``game_slug`` so the GM panel can
    prove the harness was the source of this game's first turn."""
    captured = _capture_events(monkeypatch)
    app = _build_dev_scenes_app(monkeypatch, save_dir=tmp_path)
    client = TestClient(app)

    r = client.post("/dev/scene/combat_test")
    assert r.status_code == 200

    intent_events = [e for e in captured if "scene_harness" in e[0] and "load" in e[0]]
    assert intent_events, (
        f"scene-harness must emit a load-intent span; "
        f"captured event types: {sorted({e[0] for e in captured})!r}"
    )
    # The intent span must carry the fixture name so the GM panel groups
    # events by fixture (Keith iterates many fixtures per playtest session).
    fields = intent_events[0][1]
    assert fields.get("fixture_name") == "combat_test", (
        f"load-intent span must carry fixture_name='combat_test'; got fields={fields!r}"
    )


def test_scene_harness_emits_hydrate_ok_span(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """ADR-092 §OTEL: ``scene_harness.hydrate.ok`` on success path,
    carrying field counts (npcs, characters) so the dashboard can
    confirm the fixture wasn't silently empty-hydrated."""
    captured = _capture_events(monkeypatch)
    app = _build_dev_scenes_app(monkeypatch, save_dir=tmp_path)
    client = TestClient(app)

    r = client.post("/dev/scene/combat_test")
    assert r.status_code == 200

    ok_events = [e for e in captured if "scene_harness" in e[0] and "hydrate" in e[0] and "ok" in e[0]]
    assert ok_events, (
        f"scene-harness must emit hydrate.ok on success; "
        f"captured event types: {sorted({e[0] for e in captured})!r}"
    )


def test_scene_harness_emits_persist_ok_span(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """ADR-092 §OTEL: ``scene_harness.persist.ok`` so the GM panel sees
    persistence committed (not just "we got to the end of the route")."""
    captured = _capture_events(monkeypatch)
    app = _build_dev_scenes_app(monkeypatch, save_dir=tmp_path)
    client = TestClient(app)

    r = client.post("/dev/scene/combat_test")
    assert r.status_code == 200

    persist_events = [e for e in captured if "scene_harness" in e[0] and "persist" in e[0]]
    assert persist_events, (
        f"scene-harness must emit persist.ok on success; "
        f"captured event types: {sorted({e[0] for e in captured})!r}"
    )
    # The persist span must carry the minted slug so the GM panel correlates
    # the harness-load to subsequent slug-connect events.
    slug = r.json()["slug"]
    fields = persist_events[0][1]
    assert fields.get("game_slug") == slug or fields.get("slug") == slug, (
        f"persist.ok span must carry the minted slug {slug!r}; got fields={fields!r}"
    )


def test_scene_harness_emits_hydrate_error_span_on_invalid_fixture(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """ADR-092 §OTEL: ``scene_harness.hydrate.error`` on failure path
    so the GM panel surfaces the rejected fixture without a developer
    having to tail server logs."""
    fixtures_dir = tmp_path / "fixtures"
    fixtures_dir.mkdir()
    (fixtures_dir / "no_genre.yaml").write_text("world: default\n", encoding="utf-8")
    save_dir = tmp_path / "saves"
    save_dir.mkdir()

    captured = _capture_events(monkeypatch)
    app = _build_dev_scenes_app(monkeypatch, save_dir=save_dir, fixtures_dir=fixtures_dir)
    client = TestClient(app)

    r = client.post("/dev/scene/no_genre")
    assert r.status_code == 422

    err_events = [e for e in captured if "scene_harness" in e[0] and "error" in e[0]]
    assert err_events, (
        f"scene-harness must emit a hydrate.error span on 422; "
        f"captured event types: {sorted({e[0] for e in captured})!r}"
    )
