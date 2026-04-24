from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

from sidequest.corpus.save_reader import SaveReader


@dataclass(frozen=True)
class PlayerVariant:
    source_save: str
    content: str


@dataclass(frozen=True)
class VisibilityDivergence:
    round_number: int
    variants: list[PlayerVariant]


def diff_per_player(saves: Iterable[Path]) -> Iterator[VisibilityDivergence]:
    """Emit one divergence record per round_number whose narrator content differs across saves.

    Only narrator-authored rows are compared — player actions are per-seat input, not
    divergent narration. Rounds where narrator content is byte-identical across all
    saves are skipped.
    """
    by_round: dict[int, list[PlayerVariant]] = {}
    for save in saves:
        with SaveReader(save) as reader:
            for row in reader.iter_narrative_log():
                if row.author != "narrator":
                    continue
                by_round.setdefault(row.round_number, []).append(
                    PlayerVariant(source_save=str(save), content=row.content)
                )
    for round_number in sorted(by_round):
        variants = by_round[round_number]
        contents = {v.content for v in variants}
        if len(contents) <= 1:
            continue
        yield VisibilityDivergence(round_number=round_number, variants=variants)
