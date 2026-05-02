from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sidequest.corpus.save_reader import NarrativeRow, SaveReader
from sidequest.corpus.schema import MineProvenance, TrainingPair


def _session_meta(reader: SaveReader) -> tuple[str, str]:
    """Return (genre_slug, world_slug) from session_meta. Fail loud on missing rows.

    Schema authority: sidequest/game/persistence.py — columns are named
    genre_slug / world_slug, not genre / world. The TrainingPair fields
    (genre, world) drop the _slug suffix.
    """
    row = reader.conn.execute("SELECT genre_slug, world_slug FROM session_meta LIMIT 1").fetchone()
    if row is None:
        raise RuntimeError("save.db has no session_meta row — cannot mine corpus")
    return row[0], row[1]


def _group_by_round(rows: list[NarrativeRow]) -> dict[int, list[NarrativeRow]]:
    groups: dict[int, list[NarrativeRow]] = {}
    for row in rows:
        groups.setdefault(row.round_number, []).append(row)
    return groups


def mine_save(path: Path) -> Iterator[TrainingPair]:
    """Emit one TrainingPair per round_number that has a narrator row.

    Input is the player action; output is the narrator's response. When no
    player-authored row exists for a round (e.g. opening narration), input
    falls back to the previous round's narrator output. If neither a player
    row nor a previous narration exists (round 1 with no player input), the
    round is skipped.
    """
    with SaveReader(path) as reader:
        genre, world = _session_meta(reader)
        rows = list(reader.iter_narrative_log())

    grouped = _group_by_round(rows)
    previous_narrator = ""
    for round_number in sorted(grouped):
        bucket = grouped[round_number]
        player = next((r for r in bucket if r.author != "narrator"), None)
        narrator = next((r for r in bucket if r.author == "narrator"), None)
        if narrator is None:
            continue
        input_text = player.content if player is not None else previous_narrator
        if not input_text:
            previous_narrator = narrator.content
            continue
        yield TrainingPair(
            schema_version=1,
            genre=genre,
            world=world,
            round_number=round_number,
            input_text=input_text,
            output_text=narrator.content,
            provenance=MineProvenance(source_save=str(path), event_seq=None),
        )
        previous_narrator = narrator.content
