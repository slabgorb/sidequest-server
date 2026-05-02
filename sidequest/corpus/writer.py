from __future__ import annotations

import os
import uuid
from collections.abc import Iterable
from pathlib import Path

from sidequest.corpus.schema import TrainingPair

_SAVE_ROOT = Path.home() / ".sidequest" / "saves"


def _refuse_save_overwrite(path: Path) -> None:
    """Reject output paths that would overwrite a real SQLite save.

    Real player saves in ~/.sidequest/saves/ are reference data (project
    policy: "do not fuck with them"). If the user accidentally passes one
    as --out, we refuse loudly rather than let the atomic rename destroy it.
    """
    if path.suffix == ".db":
        raise ValueError(f"refusing to write JSONL to a .db path: {path}")
    try:
        resolved = path.resolve()
        save_root = _SAVE_ROOT.resolve()
        resolved.relative_to(save_root)
    except (ValueError, OSError):
        return  # not under saves root, or path doesn't exist yet — safe
    raise ValueError(f"refusing to write to a path under {_SAVE_ROOT}: {path}")


def write_pairs(path: Path, pairs: Iterable[TrainingPair]) -> None:
    """Write pairs as JSONL atomically. Completes or raises — never half-writes.

    Guards against (a) overwriting real player saves (see _refuse_save_overwrite),
    (b) racing concurrent writers on the same target (unique tmp name per call),
    (c) leaking the .tmp file if the replace step fails.
    """
    _refuse_save_overwrite(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}.{uuid.uuid4().hex}")
    try:
        with tmp.open("w", encoding="utf-8") as fh:
            for pair in pairs:
                fh.write(pair.model_dump_json())
                fh.write("\n")
        tmp.replace(path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
