"""Dotted + [*] field-path applicator for payload dicts.

Read returns all values matched by a path (list may be empty).
Apply-mask mutates the dict in place, setting matched leaves to `mask`.

Grammar: path := segment ("." segment)*
         segment := name | name "[*]"

No support for array indices, filters, or negations.
"""

from __future__ import annotations

import re
from typing import Any

_SEGMENT = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)(\[\*\])?$")


def _parse(path: str) -> list[tuple[str, bool]]:
    segments: list[tuple[str, bool]] = []
    for seg in path.split("."):
        m = _SEGMENT.match(seg)
        if not m:
            raise ValueError(f"invalid field path segment: {seg!r}")
        segments.append((m.group(1), m.group(2) is not None))
    return segments


def read_path(payload: dict, path: str) -> list[Any]:
    segments = _parse(path)
    current: list[Any] = [payload]
    for name, is_list in segments:
        next_current: list[Any] = []
        for item in current:
            if not isinstance(item, dict):
                continue
            if name not in item:
                continue
            value = item[name]
            if is_list:
                if not isinstance(value, list):
                    continue
                next_current.extend(value)
            else:
                next_current.append(value)
        current = next_current
    return current


def apply_mask(payload: dict, path: str, *, mask: Any) -> None:
    segments = _parse(path)
    current_nodes: list[Any] = [payload]
    for idx, (name, is_list) in enumerate(segments):
        is_last = idx == len(segments) - 1
        next_nodes: list[Any] = []
        for node in current_nodes:
            if not isinstance(node, dict):
                continue
            if name not in node:
                continue
            if is_last:
                if is_list and isinstance(node[name], list):
                    node[name] = [mask for _ in node[name]]
                else:
                    node[name] = mask
            else:
                value = node[name]
                if is_list:
                    if isinstance(value, list):
                        next_nodes.extend(value)
                else:
                    next_nodes.append(value)
        current_nodes = next_nodes
