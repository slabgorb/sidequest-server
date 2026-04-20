"""OTEL telemetry for sidequest-server."""

from sidequest.telemetry.setup import init_tracer, tracer
from sidequest.telemetry import spans

__all__ = ["init_tracer", "tracer", "spans"]
