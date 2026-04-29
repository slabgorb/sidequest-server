"""Discovered-regions write-time validator (Story 45-16).

Playtest 3 Felix observed `(aside — narrator brief)` registered as a
traversable region alongside legitimate rooms. Aside rounds were
leaking into the region graph because the narrator-driven write paths
appended `result.location` and patch entries without inspecting their
shape.

This module centralizes the rejection rule so every write seam shares
the same definition of "non-room entry". When a write site rejects an
entry it MUST emit ``region.entry_rejected`` so the GM panel sees the
filter fire (CLAUDE.md OTEL Observability Principle — Sebastien needs
the rejection counted, not silently dropped).

Rejection rule (conservative, conjunctive):

- Empty / whitespace-only after strip
- Leading bracket character ``(``, ``[``, ``{``, ``<`` after lstrip
  (parenthetical narrator commentary — the original Felix leak)
- Contains a newline (multi-line text is never a region name)
- Length > 80 characters after strip (proper region names are short
  noun phrases — longest in test fixtures is "Felix's Workshop" at 16)

A valid name returns ``(True, None)``. An invalid name returns
``(False, reason)`` where ``reason`` is one of ``empty``,
``bracketed``, ``multiline``, ``too_long`` — these are the values
passed as the ``reason`` attribute on the rejection span.
"""

from __future__ import annotations

# Cap chosen so legitimate proper-noun-phrase region names pass while
# narrator prose paragraphs are blocked. 80 chars matches the longest
# observed legitimate region in fixtures plus a healthy multiplier.
_MAX_REGION_NAME_LENGTH = 80

# Opening punctuation that signals parenthetical / structural commentary
# rather than a region name. Brace and angle-bracket included because
# narrator output occasionally emits ``{system note}`` or ``<aside>``
# style asides.
_BRACKET_PREFIXES = ("(", "[", "{", "<")


def validate_region_name(name: str | None) -> tuple[bool, str | None]:
    """Return ``(is_valid, reason)`` for a candidate region entry.

    ``reason`` is ``None`` on the valid path. On the invalid path it is
    one of: ``empty``, ``bracketed``, ``multiline``, ``too_long``. The
    reason string is the value the caller passes as the ``reason``
    attribute on the ``region.entry_rejected`` OTEL span.
    """
    if name is None:
        return False, "empty"

    stripped = name.strip()
    if not stripped:
        return False, "empty"

    if "\n" in stripped or "\r" in stripped:
        return False, "multiline"

    if stripped.startswith(_BRACKET_PREFIXES):
        return False, "bracketed"

    if len(stripped) > _MAX_REGION_NAME_LENGTH:
        return False, "too_long"

    return True, None


__all__ = ["validate_region_name"]
