"""Story 50-4 — wiring test for between-session trope advancement.

The unit tests in ``tests/game/test_trope_advance_between_sessions.py``
pin the engine's contract in isolation. That's not enough. Per CLAUDE.md
("Verify Wiring, Not Just Existence" + "Every Test Suite Needs a Wiring
Test"), we must also prove the engine is actually invoked from the
production session-load path. Otherwise a perfectly working engine sits
behind a never-fired call site — exactly the half-wired feature the
project's principles forbid.

The session-load wire site is
``sidequest.handlers.connect`` — the handler that calls
``store.load()`` and binds the resulting ``GameSnapshot`` to the live
session. Between-session advancement MUST run after deserialization
(so the snapshot exists) and before the first turn dispatches.

This test inspects the module's source AND its import graph so a Dev
who only adds the call inside a never-imported branch still gets caught.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

CONNECT_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "sidequest"
    / "handlers"
    / "connect.py"
)


def _connect_source() -> str:
    return CONNECT_MODULE_PATH.read_text()


class TestEngineWiredIntoConnectHandler:
    def test_connect_module_exists(self) -> None:
        """Sanity: the wire site we're checking must actually exist.
        If somebody moves it, fix this constant rather than skip the test.
        """

        assert CONNECT_MODULE_PATH.is_file(), (
            f"expected connect handler at {CONNECT_MODULE_PATH}; the wire "
            "site must move with the test"
        )

    def test_connect_imports_advance_tropes_between_sessions(self) -> None:
        """The connect handler MUST import the engine. A module-level
        import is the cleanest signal that the engine is reachable from
        this path; a deferred/conditional import would hide the wiring
        from this static check, which is the whole point of the test.
        """

        src = _connect_source()
        tree = ast.parse(src)

        names: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith("sidequest.game.trope_advance"):
                    names.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.Import):
                names.extend(alias.name for alias in node.names)

        assert "advance_tropes_between_sessions" in names, (
            "sidequest.handlers.connect must import "
            "advance_tropes_between_sessions from sidequest.game.trope_advance — "
            "without the import, the engine is dead code and the GM panel "
            "will never see between-session spans."
        )

    def test_connect_calls_advance_tropes_between_sessions(self) -> None:
        """An import alone is not enough — the function must actually
        be *called* from connect.py. A static AST check for a Call
        node referencing the name catches the "imported but unused"
        regression.
        """

        src = _connect_source()
        tree = ast.parse(src)

        call_found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id == "advance_tropes_between_sessions":
                    call_found = True
                    break
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr == "advance_tropes_between_sessions"
                ):
                    call_found = True
                    break

        assert call_found, (
            "advance_tropes_between_sessions is imported but never called "
            "in sidequest/handlers/connect.py — the engine is wired up to "
            "nothing. Add the call after the SavedSession is deserialized "
            "and before the first turn dispatches."
        )

    def test_call_passes_loaded_snapshot(self) -> None:
        """The call must reference the loaded snapshot — passing a
        freshly-constructed GameSnapshot instead would silently advance
        an empty list every time and the bug would be invisible.
        """

        src = _connect_source()
        tree = ast.parse(src)

        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            is_target = (
                isinstance(func, ast.Name)
                and func.id == "advance_tropes_between_sessions"
            ) or (
                isinstance(func, ast.Attribute)
                and func.attr == "advance_tropes_between_sessions"
            )
            if not is_target:
                continue

            # Collect arg source forms (keyword preferred) for diagnostics.
            kw_names = {kw.arg for kw in node.keywords if kw.arg}
            assert {"snapshot", "pack", "now"} <= kw_names, (
                f"advance_tropes_between_sessions call in connect.py must "
                f"pass snapshot=, pack=, now= as keyword args; got keywords "
                f"{kw_names}. Positional args are forbidden — the engine "
                f"signature is keyword-only by design."
            )
            return

        pytest.fail(
            "could not locate advance_tropes_between_sessions call in "
            "connect.py — see test_connect_calls_advance_tropes_between_sessions"
        )


class TestSpanRoutedThroughWatcher:
    """The OTEL span is the lie detector. Per CLAUDE.md, every fix
    that touches a subsystem must add an OTEL span that the GM panel
    can see. The wiring test confirms the constant is exported AND that
    a route exists; the unit test confirms the engine emits it. Both
    layers are necessary — a span constant with no route is a leak.
    """

    def test_span_constant_reachable_from_package(self) -> None:
        import sidequest.telemetry.spans as span_pkg

        assert hasattr(span_pkg, "SPAN_TROPE_BETWEEN_SESSION_ADVANCE"), (
            "SPAN_TROPE_BETWEEN_SESSION_ADVANCE must be re-exported by "
            "sidequest.telemetry.spans (the package __init__ does "
            "star-imports per domain — missing the constant means the "
            "watcher cannot subscribe to it by name)."
        )


class TestEngineModuleHygiene:
    """Quick static checks that the new module obeys the project's
    most-violated rules. Rule numbers reference
    ``.pennyfarthing/gates/lang-review/python.md``.
    """

    def test_module_has_docstring(self) -> None:
        """Module-level docstring is the project's convention for any
        new engine — pin it so a future Dev can find the ADR-018 link
        without grep.
        """

        import sidequest.game.trope_advance as mod

        assert inspect.getdoc(mod), (
            "sidequest.game.trope_advance is missing its module docstring"
        )

    def test_public_function_has_docstring(self) -> None:
        from sidequest.game.trope_advance import advance_tropes_between_sessions

        assert inspect.getdoc(advance_tropes_between_sessions), (
            "advance_tropes_between_sessions is missing its docstring — "
            "the load handler reads this when it wires the call"
        )
