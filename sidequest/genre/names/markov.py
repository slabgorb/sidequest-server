"""Character-level Markov chain for generating fantasy words.

Adapted from Keith Avery's fantasy-language-maker (2011-2024).
Trains on text corpora and produces words that "sound like" the source language.
"""

from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from unicodedata import category


class _START:
    """Sentinel for word start."""


class _END:
    """Sentinel for word end."""


@dataclass
class MarkovChain:
    """Character-level Markov chain word generator.

    Train on text, then call make_word() to generate fantasy words
    that share the phonemic character of the training data.

    Args:
        lookback: Number of characters of context. 2 = wilder, 3 = smoother.
        rng: Optional random.Random instance for deterministic output.
    """

    lookback: int = 2
    rng: random.Random | None = None
    reject_words: set[str] = field(default_factory=set)
    _chain: dict[tuple, dict[object, int]] = field(
        default_factory=lambda: defaultdict(lambda: defaultdict(int)),
        init=False,
        repr=False,
    )

    def _random(self) -> random.Random:
        return self.rng if self.rng is not None else random._inst

    def train(self, text: str) -> None:
        """Train on raw text. Extracts words, filters to letters only."""
        for line in text.splitlines():
            for word in line.split():
                cleaned = "".join(ch for ch in word if category(ch)[0] == "L")
                if cleaned:
                    self._add_word(cleaned.lower())

    def train_file(self, text: str) -> None:
        """Train on a file's text, stripping Project Gutenberg front/back matter."""
        lines = text.splitlines()
        in_body = False
        body_lines: list[str] = []

        for line in lines:
            if not in_body:
                if "*** START" in line:
                    in_body = True
                continue
            if "*** END" in line:
                break
            body_lines.append(line)

        if not body_lines:
            self.train(text)
        else:
            self.train("\n".join(body_lines))

    def _add_word(self, word: str) -> None:
        key: list[object] = [_START] * self.lookback
        for char in word:
            self._chain[tuple(key)][char] += 1
            key.append(char)
            key.pop(0)
        self._chain[tuple(key)][_END] += 1

    def _weighted_choice(self, counts: dict[object, int]) -> object:
        rng = self._random()
        total = sum(counts.values())
        if total == 0:
            return _END
        rnd = rng.randrange(0, total)
        position = 0
        for item, count in counts.items():
            position += count
            if rnd < position:
                return item
        return _END

    def make_word(self) -> str:
        """Generate a single fantasy word."""
        if not self._chain:
            raise RuntimeError("Chain has no training data. Call train() first.")
        word = ""
        key: list[object] = [_START] * self.lookback
        for _ in range(50):
            char = self._weighted_choice(self._chain[tuple(key)])
            if char is _END:
                break
            word += str(char)
            key.append(char)
            key.pop(0)
        return word

    def make_words(
        self,
        count: int,
        min_length: int = 2,
        max_length: int = 12,
    ) -> list[str]:
        """Generate multiple unique words within length bounds."""
        words: list[str] = []
        seen: set[str] = set()
        attempts = 0
        max_attempts = count * 20

        while len(words) < count and attempts < max_attempts:
            attempts += 1
            word = self.make_word()
            if (
                min_length <= len(word) <= max_length
                and word not in seen
                and word not in self.reject_words
            ):
                seen.add(word)
                words.append(word)

        return words

    def load_reject_file(self, path: str | Path) -> None:
        """Load a dictionary file as reject words (one word per line)."""
        text = Path(path).read_text(encoding="utf-8")
        for line in text.splitlines():
            word = line.strip().lower()
            if word:
                self.reject_words.add(word)


def generate_dictionary(
    chain: MarkovChain,
    english_words: list[str],
    min_length: int = 2,
    max_length: int = 12,
) -> dict[str, str]:
    """Map each English word to a unique generated fantasy word."""
    mapping: dict[str, str] = {}
    used: set[str] = set()
    max_attempts_per_word = 50

    for eng in english_words:
        for _ in range(max_attempts_per_word):
            fantasy = chain.make_word()
            if (
                min_length <= len(fantasy) <= max_length
                and fantasy not in used
                and fantasy not in chain.reject_words
            ):
                mapping[eng] = fantasy
                used.add(fantasy)
                break

    return mapping
