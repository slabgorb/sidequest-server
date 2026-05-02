"""Strip ``[combat]`` markers from aside prose.

Port of sidequest-api/crates/sidequest-server/src/dispatch/aside.rs
``strip_combat_brackets``. Story 3.4.
"""

from __future__ import annotations

import re

_BRACKET_RE = re.compile(r"\[combat\]", flags=re.IGNORECASE)


def strip_combat_brackets(text: str) -> str:
    """Remove ``[combat]`` tags (case-insensitive) from aside text.

    Leaves any other bracketed tags alone — only the literal ``combat``
    marker is scrubbed. Eats at most one trailing space after the tag
    so ``"[combat] I swing"`` becomes ``"I swing"`` rather than ``" I swing"``.
    """
    result = _BRACKET_RE.sub("", text)
    # If text started with [, lstrip the leading space that may remain
    if text.startswith("["):
        result = result.lstrip(" ")
    return result
