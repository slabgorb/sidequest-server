"""Character generation spans — stat rolls, HP formula, backstory."""

from __future__ import annotations

from ._core import FLAT_ONLY_SPANS

SPAN_CHARGEN_STAT_ROLL = "chargen.stat_roll"
SPAN_CHARGEN_STATS_GENERATED = "chargen.stats_generated"
SPAN_CHARGEN_HP_FORMULA = "chargen.hp_formula"
SPAN_CHARGEN_BACKSTORY_COMPOSED = "chargen.backstory_composed"

FLAT_ONLY_SPANS.update({
    SPAN_CHARGEN_STAT_ROLL,
    SPAN_CHARGEN_STATS_GENERATED,
    SPAN_CHARGEN_HP_FORMULA,
    SPAN_CHARGEN_BACKSTORY_COMPOSED,
})
