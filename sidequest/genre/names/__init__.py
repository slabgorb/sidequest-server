"""sidequest.genre.names — culture-driven Markov name generation.

Port of python-sidequest/sidequest/procgen (markov + names).
"""

from sidequest.genre.names.generator import (
    NameGenerator,
    SlotGenerator,
    build_from_culture,
    translate_word_list,
)
from sidequest.genre.names.markov import MarkovChain, generate_dictionary

__all__ = [
    "MarkovChain",
    "NameGenerator",
    "SlotGenerator",
    "build_from_culture",
    "generate_dictionary",
    "translate_word_list",
]
