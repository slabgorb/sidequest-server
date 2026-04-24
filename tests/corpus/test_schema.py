from __future__ import annotations

import pytest
from pydantic import ValidationError

from sidequest.corpus.schema import (
    CORPUS_SCHEMA_VERSION,
    DisputeTag,
    LabeledPair,
    MineProvenance,
    TrainingPair,
)


def test_schema_version_is_1() -> None:
    assert CORPUS_SCHEMA_VERSION == 1


def test_training_pair_requires_input_and_output() -> None:
    pair = TrainingPair(
        schema_version=1,
        genre="caverns_and_claudes",
        world="mawdeep",
        round_number=3,
        input_text="I push on the door.",
        output_text="The door resists; something heavy braces it from the other side.",
        provenance=MineProvenance(source_save="fixtures/single_session.db", event_seq=None),
    )
    assert pair.input_text.startswith("I push")


def test_training_pair_rejects_extra_fields() -> None:
    with pytest.raises(ValidationError):
        TrainingPair(
            schema_version=1,
            genre="caverns_and_claudes",
            world="mawdeep",
            round_number=1,
            input_text="hi",
            output_text="hi",
            provenance=MineProvenance(source_save="x.db", event_seq=None),
            nonsense_field="reject me",  # type: ignore[call-arg]
        )


def test_labeled_pair_carries_keith_correction() -> None:
    base = TrainingPair(
        schema_version=1,
        genre="caverns_and_claudes",
        world="mawdeep",
        round_number=2,
        input_text="I attack the bandit.",
        output_text="Your blade finds air.",
        provenance=MineProvenance(source_save="x.db", event_seq=None),
    )
    labeled = LabeledPair(
        pair=base,
        disputes=[DisputeTag.MIS_RESOLVED_REFERENT],
        corrected_output="The bandit is not present; the only figure in the alley is the fortune teller.",
        labeler="keith",
    )
    assert DisputeTag.MIS_RESOLVED_REFERENT in labeled.disputes
    assert labeled.corrected_output.startswith("The bandit is not present")
