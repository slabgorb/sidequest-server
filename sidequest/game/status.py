"""Structured statuses with severity tier — replaces bare-string statuses.

Spec: docs/superpowers/specs/2026-04-25-dual-track-momentum-design.md §Statuses.

Severity tiers drive recovery cadence (Scratch clears at scene end; Wound at
session end; Scar requires a milestone) and — in v2 — drive the absorption
budget when an encounter dial is about to cross threshold. v1 just tracks
the shape; the absorption mechanic ships in story 5.

Migration: existing saves carry ``CreatureCore.statuses`` as ``list[str]``.
``migrate_legacy_statuses`` converts a bare string to
``Status(text=<s>, severity=Scratch, absorbed_shifts=0, created_turn=0,
created_in_encounter=None)`` so loaders can call it during
``model_validator(mode="before")`` on CreatureCore.
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class StatusSeverity(str, Enum):  # noqa: UP042 — matches project convention (see protocol/enums.py)
    """Status severity tier — drives recovery cadence and (v2) absorption budget."""

    Scratch = "Scratch"
    Wound = "Wound"
    Scar = "Scar"


class Status(BaseModel):
    """An actor-level lingering cost.

    ``absorbed_shifts`` is 0 in v1; story 5 sets it from the severity's
    absorption budget when the status absorbs a would-be threshold cross.
    """

    model_config = {"extra": "forbid"}

    text: str
    severity: StatusSeverity
    absorbed_shifts: int = 0
    created_turn: int = 0
    created_in_encounter: str | None = None


def migrate_legacy_statuses(raw: list[object]) -> list[Status]:
    """Forward-migrate a save's ``statuses`` field to structured Status list.

    Accepts a list whose entries are either bare ``str`` (legacy save) or
    already-structured ``Status`` instances (post-migration save). A list
    that contains anything else raises ``TypeError`` per CLAUDE.md
    "no silent fallbacks".
    """
    out: list[Status] = []
    for entry in raw:
        if isinstance(entry, Status):
            out.append(entry)
            continue
        if isinstance(entry, str):
            out.append(
                Status(
                    text=entry,
                    severity=StatusSeverity.Scratch,
                    absorbed_shifts=0,
                    created_turn=0,
                    created_in_encounter=None,
                )
            )
            continue
        if isinstance(entry, dict):
            out.append(Status.model_validate(entry))
            continue
        raise TypeError(
            f"unexpected entry in statuses list: {entry!r} "
            f"(type={type(entry).__name__}); "
            f"expected str, dict, or Status"
        )
    return out
