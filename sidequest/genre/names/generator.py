"""Template-based name generator with corpus blending.

Combines Markov chain word generation with cultural naming patterns.
Each culture defines slots (given_name, family_name, etc.) that can draw from
Markov-trained corpora or static word lists. Templates like "{given_name} de {family_name}"
assemble slots into full names.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path

from sidequest.genre.models.culture import Culture
from sidequest.genre.names.markov import MarkovChain, generate_dictionary


@dataclass
class SlotGenerator:
    """Generates words for a single naming slot."""

    chain: MarkovChain | None = None
    word_list: list[str] = field(default_factory=list)
    min_length: int = 4
    max_length: int = 12
    rng: random.Random | None = None

    def _random(self) -> random.Random:
        return self.rng if self.rng is not None else random._inst

    def generate(self) -> str:
        rng = self._random()

        has_chain = self.chain is not None
        has_list = bool(self.word_list)

        if not has_chain and not has_list:
            return ""

        if has_chain and has_list:
            use_chain = rng.random() < 0.67
        elif has_chain:
            use_chain = True
        else:
            use_chain = False

        if use_chain and self.chain is not None:
            for _ in range(20):
                word = self.chain.make_word()
                if (
                    self.min_length <= len(word) <= self.max_length
                    and word not in self.chain.reject_words
                ):
                    return word
            return self.chain.make_word()
        else:
            return rng.choice(self.word_list)


class _SlotMap:
    """Lazy dict-like object for str.format_map() with per-slot caching."""

    def __init__(self, slots: dict[str, SlotGenerator]) -> None:
        self._slots = slots
        self._cache: dict[str, str] = {}

    def __getitem__(self, key: str) -> str:
        if key not in self._cache:
            gen = self._slots.get(key)
            if gen is None:
                return "{" + key + "}"
            self._cache[key] = gen.generate()
        return self._cache[key]


@dataclass
class NameGenerator:
    """Generates names from template patterns using slot generators."""

    slots: dict[str, SlotGenerator] = field(default_factory=dict)
    person_patterns: list[str] = field(default_factory=list)
    place_patterns: list[str] = field(default_factory=list)
    rng: random.Random | None = None

    def _random(self) -> random.Random:
        return self.rng if self.rng is not None else random._inst

    def generate_person(self, pattern: str | None = None) -> str:
        if pattern is None:
            if not self.person_patterns:
                raise ValueError("No person patterns configured")
            pattern = self._random().choice(self.person_patterns)
        return self._fill(pattern)

    def generate_place(self, pattern: str | None = None) -> str:
        if pattern is None:
            if not self.place_patterns:
                raise ValueError("No place patterns configured")
            pattern = self._random().choice(self.place_patterns)
        return self._fill(pattern)

    def _fill(self, pattern: str) -> str:
        slot_map = _SlotMap(self.slots)
        result = pattern.format_map(slot_map)
        return _titlecase_name(result)


def translate_word_list(word_list: list[str], dictionary: dict[str, str]) -> list[str]:
    """Replace words in word_list using dictionary, passing through unknowns."""
    return [dictionary.get(word, word) for word in word_list]


def _titlecase_name(name: str) -> str:
    """Title-case a name, keeping small words lowercase."""
    small_words = {"de", "of", "the", "and", "le", "la", "von", "van", "du", "des"}
    parts = name.split()
    result = []
    for i, part in enumerate(parts):
        if i == 0 or part.lower() not in small_words:
            result.append(part.capitalize())
        else:
            result.append(part.lower())
    return " ".join(result)


def build_from_culture(
    culture: Culture,
    corpus_dir: Path,
    rng: random.Random | None = None,
    chain_cache: dict[tuple[str, int], str] | None = None,
) -> NameGenerator:
    """Build a NameGenerator from a Culture and corpus directory.

    All corpora for a slot are trained into a single MarkovChain so that
    character transitions blend at the phonemic level. This produces words
    that don't exist in either source language.

    Args:
        culture: A Culture model instance (from genre pack models).
        corpus_dir: Path to the genre pack's corpus/ directory.
        rng: Optional RNG for deterministic output.
        chain_cache: Optional cache of raw corpus text keyed by
                     (corpus_filename, lookback).
    """
    if chain_cache is None:
        chain_cache = {}

    slots: dict[str, SlotGenerator] = {}

    for slot_name, slot_config in culture.slots.items():
        chain: MarkovChain | None = None
        lookback = slot_config.lookback if slot_config.lookback is not None else 2

        # Build local word list (never mutate the shared pydantic model).
        word_list: list[str] = list(slot_config.word_list or [])

        if slot_config.names_file:
            names_path = corpus_dir.parent / "names" / slot_config.names_file
            if not names_path.exists():
                raise FileNotFoundError(
                    f"Names file '{slot_config.names_file}' not found at {names_path}"
                )
            word_list = [
                line.strip()
                for line in names_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

        if slot_config.corpora:
            chain = MarkovChain(lookback=lookback, rng=rng)

            for corpus_ref in slot_config.corpora:
                corpus_path = corpus_dir / corpus_ref.corpus
                if not corpus_path.exists():
                    raise FileNotFoundError(
                        f"Corpus file '{corpus_ref.corpus}' not found at {corpus_path}"
                    )

                cache_key = (corpus_ref.corpus, lookback)
                if cache_key not in chain_cache:
                    chain_cache[cache_key] = corpus_path.read_text(encoding="utf-8")

                text = chain_cache[cache_key]
                rounds = max(1, round(corpus_ref.weight))
                for _ in range(rounds):
                    chain.train_file(text)

            for reject_file in slot_config.reject_files:
                reject_path = corpus_dir / reject_file
                if reject_path.exists():
                    chain.load_reject_file(reject_path)

        # If the slot has both a word_list and corpora, translate the word list
        # into fantasy equivalents using the trained chain.
        if word_list and chain is not None:
            generated = generate_dictionary(chain, word_list)
            word_list = translate_word_list(word_list, generated)

        slots[slot_name] = SlotGenerator(
            chain=chain,
            word_list=word_list,
            rng=rng,
        )

    return NameGenerator(
        slots=slots,
        person_patterns=list(culture.person_patterns),
        place_patterns=list(culture.place_patterns),
        rng=rng,
    )
