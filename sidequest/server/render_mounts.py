"""Self-healing render-asset mount registry.

The daemon writes generated images to a tmpdir whose path changes on every
daemon restart (``/var/folders/.../sq-daemon-XXXX/zimage/``). The server
exposes those files via a ``/renders/*`` static-file mount. If the daemon
restarts mid-session, the server's mount stays pinned to the *old* tmpdir
and every new render 404s — that's the playtest 2026-04-25 [S4-BUG]
regression.

This module implements the architect-approved Option-2 fix:

* On every render-completed message, before building the URL we hand to
  the UI, call :func:`ensure_render_mount` with the absolute filesystem
  path the daemon returned.
* The registry computes the daemon root that contains the file, and if
  that root isn't already served, appends it to the live ``StaticFiles``
  app's directory list (``all_directories``). Starlette's
  ``lookup_path`` walks that list at request time, so newly-appended
  roots are picked up without restart.
* The URL builder (``url_for_path``) translates the absolute path into a
  ``/renders/<rel-to-root>`` URL using the *matching* root — which may
  differ from ``SIDEQUEST_OUTPUT_DIR`` after a daemon restart.

Invariants:

* Idempotent: registering the same root twice is a no-op.
* Old roots are never unmounted — in-flight URLs stay valid until the
  underlying tmpdir is reaped by the OS.
* If a path can't be matched to any known root we emit
  ``image_unavailable`` (forensics, CLAUDE.md OTEL principle).
* If a new root has to be registered post-handshake, we emit
  ``render_assets.mount_remounted`` so the GM panel sees the
  self-healing event. That's the lie detector for "did the fix actually
  fire when the daemon restarted?"
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from threading import Lock

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.routing import Mount

logger = logging.getLogger(__name__)

# Process-singleton state. The server creates exactly one FastAPI app per
# uvicorn worker; tests construct ephemeral apps via ``create_app`` and
# we want each test's mount registry to be isolated. So we key the state
# by id(app) and clean up via ``reset_for_app`` from the test fixture
# when needed. The lock guards mutation of the shared dict + the
# StaticFiles ``all_directories`` list (which Starlette doesn't lock).
_LOCK = Lock()
_REGISTERED_ROOTS: dict[int, set[Path]] = {}
_ACTIVE_APP: FastAPI | None = None


def set_active_app(app: FastAPI | None) -> None:
    """Register ``app`` as the process-singleton FastAPI used by
    render-mount lookups from non-DI contexts (background render
    coroutines in ``session_handler``).

    Called once from :func:`create_app` after the initial mount setup.
    Tests that build ephemeral apps can ignore this — they wire the app
    explicitly into the helpers. Pass ``None`` from a test fixture to
    clear the binding between runs.
    """
    global _ACTIVE_APP
    _ACTIVE_APP = app


def get_active_app() -> FastAPI | None:
    """Return the singleton app registered by :func:`set_active_app`,
    or ``None`` in pure-unit-test contexts (which call helpers with an
    explicit ``app`` argument)."""
    return _ACTIVE_APP


def _resolve(p: str | os.PathLike[str]) -> Path:
    """Resolve symlinks + normalise. macOS routinely hands us
    ``/var/folders/...`` which is a symlink to ``/private/var/folders/...``
    — without ``resolve()`` the equality check below misses every macOS
    daemon root."""
    return Path(p).resolve()


def _find_static_route(app: FastAPI) -> Mount | None:
    """Locate the ``render_assets`` Mount on the app, if any.

    Returns ``None`` when the initial ``create_app`` mount was skipped
    (no env, no handshake) — in that case we'll register the mount
    on-demand.
    """
    for route in app.routes:
        if isinstance(route, Mount) and getattr(route, "name", None) == "render_assets":
            return route
    return None


def _registered_for(app: FastAPI) -> set[Path]:
    key = id(app)
    bucket = _REGISTERED_ROOTS.get(key)
    if bucket is None:
        bucket = set()
        _REGISTERED_ROOTS[key] = bucket
        # Seed with the directory the initial mount was created against,
        # so the first daemon-served URL doesn't get falsely tagged as
        # "remounted".
        existing = _find_static_route(app)
        if existing is not None and isinstance(existing.app, StaticFiles):
            for d in existing.app.all_directories:
                bucket.add(_resolve(d))
    return bucket


def reset_for_app(app: FastAPI) -> None:
    """Drop the per-app registry — used by tests to isolate runs."""
    _REGISTERED_ROOTS.pop(id(app), None)


def register_root(app: FastAPI, root: str | os.PathLike[str]) -> bool:
    """Ensure the given filesystem root is served via ``/renders/*``.

    Returns ``True`` when a new mount/append happened, ``False`` when the
    root was already known (idempotent). Raises ``FileNotFoundError`` if
    the root doesn't exist on disk — no silent fallbacks (CLAUDE.md).
    """
    resolved = _resolve(root)
    if not resolved.is_dir():
        raise FileNotFoundError(
            f"render_assets.register_root: directory does not exist: {resolved}"
        )

    with _LOCK:
        bucket = _registered_for(app)
        if resolved in bucket:
            return False

        existing = _find_static_route(app)
        if existing is None:
            # No initial mount was created (handshake-less startup). Mount now.
            sf = StaticFiles(directory=str(resolved), check_dir=False)
            app.mount("/renders", sf, name="render_assets")
            bucket.add(resolved)
            logger.info(
                "render_assets.mount_registered dir=%s source=on_demand",
                resolved,
            )
            _publish_remount(resolved, source="on_demand", first=True)
            return True

        sf = existing.app
        if not isinstance(sf, StaticFiles):
            # Defensive — should not happen given the mount factory, but
            # never silently degrade.
            raise RuntimeError("render_assets.register_root: mount target is not StaticFiles")

        # Append; Starlette's lookup_path walks all_directories at request
        # time so this is picked up without a server restart.
        sf.all_directories.append(str(resolved))
        bucket.add(resolved)
        logger.info(
            "render_assets.mount_remounted dir=%s prior=%s",
            resolved,
            [str(d) for d in sorted(bucket - {resolved})],
        )
        _publish_remount(resolved, source="render_completed", first=False)
        return True


def url_for_path(app: FastAPI, image_path: str) -> str | None:
    """Translate ``image_path`` (absolute filesystem path returned by the
    daemon) into a ``/renders/<rel>`` URL.

    Returns ``None`` if the path doesn't live under any known root —
    callers MUST treat that as "couldn't rewrite, surface to user as
    error and emit OTEL". We deliberately do not fall back to returning
    the absolute path here; the caller decides the failure-path UX.
    """
    if not image_path:
        return None
    resolved = _resolve(image_path)
    bucket = _registered_for(app)
    # Prefer the longest-matching root (more specific wins) so nested
    # roots can coexist deterministically.
    candidates = sorted(
        (root for root in bucket if _is_under(resolved, root)),
        key=lambda p: len(p.parts),
        reverse=True,
    )
    if not candidates:
        return None
    rel = resolved.relative_to(candidates[0])
    return "/renders/" + str(rel).replace(os.sep, "/")


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def ensure_render_mount(app: FastAPI, image_path: str) -> str | None:
    """One-shot: discover the root for ``image_path``, mount it if new,
    return the ``/renders/*`` URL.

    Returns ``None`` if the root can't be located on disk or
    ``image_path`` is empty — callers MUST emit ``image_unavailable``
    and propagate the failure (do not silently fall back to handing the
    UI an absolute filesystem path).
    """
    if not image_path:
        return None
    resolved = _resolve(image_path)
    if not resolved.is_file():
        # Daemon told us about a path that doesn't exist on disk; refuse
        # to mount its parent — would just register an empty dir and the
        # 404 would still happen, but with the noise of a phantom mount.
        return None

    # Already-known root? Just translate.
    url = url_for_path(app, image_path)
    if url is not None:
        return url

    # Walk up the path looking for a parent directory we can mount.
    # Strategy: mount the daemon's output-dir root — typically two
    # parents up (``.../sq-daemon-XXX/zimage/render_abc.png`` →
    # ``.../sq-daemon-XXX/``). Walking from the file ensures we don't
    # over-mount (e.g. ``/var/folders``).
    candidate = resolved.parent
    # Cap the climb at 4 levels — daemon paths are
    # ``<tmp-root>/sq-daemon-XXX/zimage/render_abc.png`` which is 2
    # parents from the file; cap protects against a malformed path
    # walking us up to ``/``.
    for _ in range(4):
        try:
            register_root(app, candidate)
            break
        except FileNotFoundError:
            candidate = candidate.parent
            continue
    else:
        return None

    return url_for_path(app, image_path)


def _publish_remount(root: Path, *, source: str, first: bool) -> None:
    """Lazy-import publish_event to avoid a server↔telemetry import
    cycle at module load."""
    try:
        from sidequest.telemetry.watcher_hub import publish_event
    except ImportError:
        return
    publish_event(
        "render_assets.mount_remounted",
        {
            "dir": str(root),
            "source": source,
            "first": first,
        },
        component="render",
        severity="info",
    )


def publish_url_404(url: str) -> None:
    """Emit a watcher event the first time we observe a 404 on
    ``/renders/*``. Forensics-only — should be rare post-fix.

    Public so middleware (or test instrumentation) can call it; we
    de-duplicate per URL to avoid flooding the dashboard.
    """
    with _LOCK:
        seen = getattr(publish_url_404, "_seen", set())
        if url in seen:
            return
        seen.add(url)
        publish_url_404._seen = seen  # type: ignore[attr-defined]
    try:
        from sidequest.telemetry.watcher_hub import publish_event
    except ImportError:
        return
    publish_event(
        "render_assets.url_404",
        {"url": url},
        component="render",
        severity="warning",
    )
