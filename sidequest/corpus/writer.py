from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from sidequest.corpus.schema import TrainingPair


def write_pairs(path: Path, pairs: Iterable[TrainingPair]) -> None:
    """Write pairs as JSONL atomically. Completes or raises — never half-writes."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with tmp.open("w", encoding="utf-8") as fh:
        for pair in pairs:
            fh.write(pair.model_dump_json())
            fh.write("\n")
    tmp.replace(path)
