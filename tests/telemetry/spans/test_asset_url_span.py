"""Verify the asset_url span emits with expected attributes."""
from __future__ import annotations

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from sidequest.telemetry.spans.asset_url import (
    SPAN_ASSET_URL_RESOLVED,
    asset_url_resolved_span,
)


def test_span_records_attrs() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer(__name__)

    with asset_url_resolved_span(
        relative_path="genre_packs/cav/audio/x.ogg",
        base_url="https://cdn.slabgorb.com",
        mode="cdn",
        _tracer=tracer,
    ):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    span = spans[0]
    assert span.name == SPAN_ASSET_URL_RESOLVED
    assert span.attributes is not None
    assert span.attributes["asset.relative_path"] == "genre_packs/cav/audio/x.ogg"
    assert span.attributes["asset.base_url"] == "https://cdn.slabgorb.com"
    assert span.attributes["asset.mode"] == "cdn"
