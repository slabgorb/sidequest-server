"""Turn-reminder spans — spawn and fire."""

from __future__ import annotations

from ._core import FLAT_ONLY_SPANS

SPAN_REMINDER_SPAWNED = "reminder_spawned"
SPAN_REMINDER_FIRED = "reminder_fired"

FLAT_ONLY_SPANS.update({SPAN_REMINDER_SPAWNED, SPAN_REMINDER_FIRED})
