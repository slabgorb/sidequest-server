"""Span helper — opens OTEL spans with attribute boilerplate centralised."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace


class Span:
    """Open OTEL spans with typed attributes."""

    @staticmethod
    @contextmanager
    def open(
        name: str,
        attrs: dict[str, Any] | None = None,
        *,
        tracer_override: trace.Tracer | None = None,
    ) -> Iterator[trace.Span]:
        """Open ``name`` as the current span with ``attrs`` set verbatim.

        When ``tracer_override`` is None the default tracer is looked up via
        :mod:`sidequest.telemetry.spans` (lazy import) so test fixtures that
        monkeypatch ``spans.tracer`` to install an in-memory exporter still
        intercept the default path.
        """
        if tracer_override is None:
            from sidequest.telemetry import spans as _spans
            tracer_override = _spans.tracer()
        with tracer_override.start_as_current_span(name, attributes=attrs or {}) as span:
            yield span
