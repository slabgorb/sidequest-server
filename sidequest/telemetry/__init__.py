"""OTEL telemetry for sidequest-server."""

from sidequest.telemetry.setup import init_tracer, tracer

__all__ = ["init_tracer", "tracer"]
