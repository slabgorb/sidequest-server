"""2nd-person POV swap for narration prose (Story 49-8).

Found in the 2026-05-12 caverns_sunden playtest: every per-PC narration
card landed on every player's tab third-person. On Carl's own tab his
action card should read "You plant a boot..." not "Carl plants a boot...".

This module rewrites third-person references to a single named target
into second-person. Pure string transform — no network, no LLM. Called
by ``sidequest.server.emitters.emit_event`` once per recipient when the
recipient's PC name matches ``visibility_sidecar["anchor_pc"]``.

Dialogue inside double quotes is preserved unchanged — speakers
referring to the target by name belong to the in-world scene, not the
narrator voice.

Contract:

    swap_to_second_person(text, target_name="Carl", pronouns="he/him")
        -> ("You plant a boot...", 5)

Returns the rewritten text and a count of distinct substitutions for
the OTEL span ``narration.second_person_swap`` (GM-panel lie detector).
"""

from __future__ import annotations

import re

from opentelemetry import trace

_tracer = trace.get_tracer("sidequest.pov_swap")

# Pronoun forms keyed by canonical pronoun string.
# Only three canonical sets are supported — anything else raises so
# downstream prose never silently uses the wrong grammar.
_PRONOUN_FORMS = {
    "he/him": {
        "subject": "he",
        "object": "him",
        "possessive": "his",
        "reflexive": ("himself",),
        # When "his" appears, both possessive and predicate-adjective
        # forms map to "your" — there is no possessive-pronoun "his" vs
        # "his own" ambiguity to resolve here. Predicate "his" (as in
        # "the gun was his") also maps to "yours" but is not currently
        # tested; we err toward the more common possessive case.
        "possessive_alt_subject_form": None,  # "his" is not ambiguous
    },
    "she/her": {
        "subject": "she",
        "object": "her",
        "possessive": "her",  # same surface form as the object — disambiguated by lookahead
        "reflexive": ("herself",),
        "possessive_alt_subject_form": None,
    },
    "they/them": {
        "subject": "they",
        "object": "them",
        "possessive": "their",
        "reflexive": ("themself", "themselves"),
        "possessive_alt_subject_form": None,
    },
}

# Irregular verbs that need explicit 3rd-person -> 2nd-person mapping.
# Regular -s/-es/-ies suffixes are handled by _conjugate's algorithmic
# fallback.
_IRREGULAR_VERBS: dict[str, str] = {
    "has": "have",
    "is": "are",
    "was": "were",
    "does": "do",
    "goes": "go",
}


def _conjugate(verb: str) -> str:
    """Convert a 3rd-person-singular verb to its 2nd-person form.

    Examples:
        plants -> plant
        watches -> watch
        tries -> try
        has -> have
        is -> are
    """
    if not verb:
        return verb
    lower = verb.lower()
    if lower in _IRREGULAR_VERBS:
        replacement = _IRREGULAR_VERBS[lower]
        # Preserve capitalization of the first letter.
        if verb[0].isupper():
            return replacement[0].upper() + replacement[1:]
        return replacement
    if lower.endswith("ies") and len(lower) > 3:
        return verb[:-3] + "y"
    if lower.endswith(("sses", "shes", "ches", "xes", "zes")):
        return verb[:-2]
    if lower.endswith("s") and not lower.endswith("ss"):
        return verb[:-1]
    return verb


def _looks_like_verb(word: str) -> bool:
    """Heuristic: does ``word`` look like a 3rd-person-singular verb?

    Used to decide whether to conjugate the word following a subject
    swap. Conservative — non-verbs that happen to end in -s (plural
    nouns, possessives) will be left alone unless they're in a position
    that clearly demands a verb.
    """
    if not word:
        return False
    lower = word.lower()
    if lower in _IRREGULAR_VERBS:
        return True
    # Word ends in -s but not -ss is the basic 3rd-person-singular signal.
    # Don't treat plural nouns ending in -ies as verbs unless context demands.
    return lower.endswith("s") and not lower.endswith("ss")


def _split_by_dialogue(text: str) -> list[tuple[str, str]]:
    """Split text into alternating prose / dialogue regions.

    Dialogue is anything inside straight double quotes (``"...".``).
    Returns a list of (kind, segment) pairs where ``kind`` is
    ``"prose"`` or ``"dialogue"``. Concatenating the segments
    reconstructs the input verbatim.
    """
    parts: list[tuple[str, str]] = []
    last = 0
    for m in re.finditer(r'"[^"]*"', text):
        if m.start() > last:
            parts.append(("prose", text[last : m.start()]))
        parts.append(("dialogue", m.group(0)))
        last = m.end()
    if last < len(text):
        parts.append(("prose", text[last:]))
    return parts


def _split_into_sentences(prose: str) -> list[str]:
    """Split a prose region into sentences, keeping each sentence's
    trailing punctuation attached.

    Splits on ``.`` ``?`` ``!`` followed by whitespace or end-of-string.
    Preserves the original whitespace/punctuation so concatenation
    reproduces the input verbatim.
    """
    # Pattern captures each sentence plus its trailing punctuation and
    # the whitespace that follows (so re-joining preserves spacing).
    pieces = re.split(r"(?<=[.!?])(\s+)", prose)
    # ``re.split`` returns alternating sentence / whitespace tokens.
    # Re-assemble back to "sentence with trailing space" tokens.
    sentences: list[str] = []
    i = 0
    while i < len(pieces):
        sent = pieces[i]
        trailing = pieces[i + 1] if (i + 1) < len(pieces) else ""
        if sent or trailing:
            sentences.append(sent + trailing)
        i += 2
    return sentences


def _rewrite_sentence(
    sentence: str,
    *,
    target_name: str,
    forms: dict,
) -> tuple[str, int]:
    """Apply all POV substitutions to a single sentence.

    Returns ``(rewritten_sentence, count)`` where ``count`` is the total
    number of substitutions performed (used for the OTEL swap_count
    attribute).
    """
    count = 0
    text = sentence
    had_subject_swap = False

    name_esc = re.escape(target_name)

    # ------------------------------------------------------------------
    # Pass 1: possessive name "Carl's" -> "Your"/"your"
    # ------------------------------------------------------------------
    def _pos_name_sub(m: re.Match) -> str:
        nonlocal count
        count += 1
        at_start = (m.start() == 0) or _is_sentence_start_in(text, m.start())
        return "Your" if at_start else "your"

    text = re.sub(rf"\b{name_esc}'s\b", _pos_name_sub, text)

    # ------------------------------------------------------------------
    # Pass 2: subject name + immediate verb. "Carl plants" -> "You plant".
    # We swap the name and conjugate the verb in one pass so the
    # verb-following-the-subject is reliably handled.
    # ------------------------------------------------------------------
    def _name_subj_sub(m: re.Match) -> str:
        nonlocal count, had_subject_swap
        had_subject_swap = True
        verb = m.group(1)
        at_start = (m.start() == 0) or _is_sentence_start_in(text, m.start())
        you = "You" if at_start else "you"
        conjugated = _conjugate(verb)
        # Count the subject swap; count the verb conjugation separately
        # when it actually changes the verb form so swap_count reflects
        # the true number of edits.
        count += 1
        if conjugated != verb:
            count += 1
        return f"{you} {conjugated}"

    text = re.sub(rf"\b{name_esc}\b\s+(\w+)", _name_subj_sub, text)

    # ------------------------------------------------------------------
    # Pass 3: bare name (no following verb) -> "you"/"You". Catches
    # vocative or trailing-clause uses like "...nodded at Carl."
    # ------------------------------------------------------------------
    def _name_bare_sub(m: re.Match) -> str:
        nonlocal count
        count += 1
        at_start = (m.start() == 0) or _is_sentence_start_in(text, m.start())
        return "You" if at_start else "you"

    text = re.sub(rf"\b{name_esc}\b", _name_bare_sub, text)

    # ------------------------------------------------------------------
    # Pass 4: reflexive ("himself"/"herself"/"themself"/"themselves") -> "yourself"
    # ------------------------------------------------------------------
    for reflexive in forms["reflexive"]:
        count_before = count

        def _reflexive_sub(m: re.Match) -> str:
            nonlocal count
            count += 1
            return "yourself"

        text, n = re.subn(rf"\b{re.escape(reflexive)}\b", _reflexive_sub, text)
        # subn returns the count separately — but our nested function
        # already incremented; reset by removing the auto-count and
        # using subn's count. Simpler: subtract our increment, add subn.
        # (re.subn here is the authoritative count.)
        count = count_before + n
        text = text  # subn already replaced

    # ------------------------------------------------------------------
    # Pass 5: subject pronoun ("he"/"she"/"they") + verb -> "you" + plural-verb
    # Conjugate the immediately-following verb just like Pass 2 did
    # for the name.
    # ------------------------------------------------------------------
    subj_pron = forms["subject"]
    subj_pat = rf"\b({subj_pron[0].upper()}{subj_pron[1:]}|{subj_pron})\b\s+(\w+)"

    def _subj_pron_sub(m: re.Match) -> str:
        nonlocal count, had_subject_swap
        had_subject_swap = True
        leader = m.group(1)
        verb = m.group(2)
        you = "You" if leader[0].isupper() else "you"
        count += 1
        if _looks_like_verb(verb):
            conjugated = _conjugate(verb)
            if conjugated != verb:
                count += 1
            return f"{you} {conjugated}"
        return f"{you} {verb}"

    text = re.sub(subj_pat, _subj_pron_sub, text)

    # ------------------------------------------------------------------
    # Pass 6: possessive pronoun ("his"/"their"/"her" before a noun) -> "your"
    # For she/her, "her" is ambiguous (object vs possessive). Disambiguate
    # by lookahead: "her" followed by whitespace + word (not punctuation)
    # is possessive; otherwise it's object (handled in Pass 7).
    # ------------------------------------------------------------------
    possessive = forms["possessive"]
    if possessive == "her":
        # She/her case: possessive "her" only when followed by a word.
        pos_pat = r"\b([Hh])er\b(?=\s+\w)"

        def _pos_her_sub(m: re.Match) -> str:
            nonlocal count
            count += 1
            return "Your" if m.group(1).isupper() else "your"

        text = re.sub(pos_pat, _pos_her_sub, text)
    elif possessive == "his":
        pos_pat = r"\b([Hh])is\b"

        def _pos_his_sub(m: re.Match) -> str:
            nonlocal count
            count += 1
            return "Your" if m.group(1).isupper() else "your"

        text = re.sub(pos_pat, _pos_his_sub, text)
    elif possessive == "their":
        pos_pat = r"\b([Tt])heir\b"

        def _pos_their_sub(m: re.Match) -> str:
            nonlocal count
            count += 1
            return "Your" if m.group(1).isupper() else "your"

        text = re.sub(pos_pat, _pos_their_sub, text)

    # ------------------------------------------------------------------
    # Pass 7: object pronoun ("him"/"them"/"her" at end of clause) -> "you"
    # For "her" specifically, this fires only when NOT followed by a noun
    # (Pass 6 already consumed the possessive case).
    # ------------------------------------------------------------------
    obj_pron = forms["object"]
    if obj_pron == "her":
        # Object "her" — followed by punctuation, end-of-string, or
        # connective words that signal end of clause.
        obj_pat = r"\b([Hh])er\b(?![\s]+\w)"

        def _obj_her_sub(m: re.Match) -> str:
            nonlocal count
            count += 1
            return "You" if m.group(1).isupper() else "you"

        text = re.sub(obj_pat, _obj_her_sub, text)
    else:
        obj_pat = rf"\b({obj_pron[0].upper()}{obj_pron[1:]}|{obj_pron})\b"

        def _obj_sub(m: re.Match) -> str:
            nonlocal count
            count += 1
            return "You" if m.group(1)[0].isupper() else "you"

        text = re.sub(obj_pat, _obj_sub, text)

    # ------------------------------------------------------------------
    # Pass 8: "and <verb>" continuation. When this sentence had a
    # subject swap earlier, the implicit subject after "and" is still
    # "you" — conjugate the verb if it's in 3rd-person form.
    # ------------------------------------------------------------------
    if had_subject_swap:

        def _and_verb_sub(m: re.Match) -> str:
            nonlocal count
            verb = m.group(1)
            if not _looks_like_verb(verb):
                return m.group(0)
            count += 1
            return f"and {_conjugate(verb)}"

        text = re.sub(r"\band\s+(\w+)", _and_verb_sub, text)

    # ------------------------------------------------------------------
    # Pass 9: ", <verb>" comma-coordinated continuation. Same logic as
    # Pass 8 but for verb-coordination through commas instead of "and".
    # English narration commonly chains actions across commas without
    # repeating the subject: "Willes thumbs the flap, sets the fitting,
    # and works the curl onto parchment." Pass 2 catches "thumbs"; Pass
    # 8 catches "works"; without this pass "sets" stays in 3rd-person
    # form and the rewritten prose reads "you thumb..., sets..., and
    # work..." — mixed conjugation in a single sentence
    # (sq-playtest 2026-05-15).
    #
    # Gated by ``had_subject_swap`` so we don't conjugate commas that
    # AREN'T verb-coordination (appositives, parentheticals, relative-
    # clause boundaries). Further gated by ``_looks_like_verb`` so plural
    # nouns or commas-before-articles ("..., the bronze fitting") pass
    # through unchanged.
    # ------------------------------------------------------------------
    if had_subject_swap:

        def _comma_verb_sub(m: re.Match) -> str:
            nonlocal count
            verb = m.group(1)
            if not _looks_like_verb(verb):
                return m.group(0)
            # Don't conjugate "and" itself if the regex happens to catch
            # ", and " — Pass 8 owns the "and <verb>" surface.
            if verb.lower() == "and":
                return m.group(0)
            conjugated = _conjugate(verb)
            if conjugated == verb:
                return m.group(0)
            count += 1
            return f", {conjugated}"

        text = re.sub(r",\s+(\w+)", _comma_verb_sub, text)

    return text, count


def _is_sentence_start_in(text: str, idx: int) -> bool:
    """Return True if position ``idx`` in ``text`` is the start of a
    sentence (beginning of string, or preceded by ``.!?`` after
    skipping whitespace).
    """
    if idx == 0:
        return True
    j = idx - 1
    while j >= 0 and text[j].isspace():
        j -= 1
    if j < 0:
        return True
    return text[j] in ".!?"


def swap_to_second_person(
    text: str,
    *,
    target_name: str,
    pronouns: str,
) -> tuple[str, int]:
    """Rewrite third-person references to ``target_name`` into second-person.

    Args:
        text: Narration prose (may contain dialogue in double quotes,
            which is left unchanged).
        target_name: The PC name to swap to "You". Must be non-empty.
        pronouns: One of ``"he/him"``, ``"she/her"``, ``"they/them"``.
            Drives pronoun substitution and reflexive choice.

    Returns:
        ``(rewritten_text, swap_count)`` — the count is the total number
        of substitutions performed across all passes (subject swaps,
        pronouns, reflexives, possessives, verb conjugations after
        ``and``). Used as the OTEL ``swap_count`` attribute.

    Raises:
        ValueError: If ``target_name`` is empty or ``pronouns`` is not
            one of the supported canonical strings. Silent fallback
            would inject wrong grammar into player-facing prose; fail
            loud per project policy.
    """
    if not target_name:
        raise ValueError("target_name must be non-empty")
    if pronouns not in _PRONOUN_FORMS:
        raise ValueError(
            f"unsupported pronouns: {pronouns!r}; supported: {sorted(_PRONOUN_FORMS.keys())}"
        )

    forms = _PRONOUN_FORMS[pronouns]
    total_count = 0

    out_parts: list[str] = []
    for kind, segment in _split_by_dialogue(text):
        if kind == "dialogue":
            out_parts.append(segment)
            continue
        # Process each sentence in this prose region.
        sentence_parts: list[str] = []
        for sentence in _split_into_sentences(segment):
            new_sent, count = _rewrite_sentence(
                sentence,
                target_name=target_name,
                forms=forms,
            )
            sentence_parts.append(new_sent)
            total_count += count
        out_parts.append("".join(sentence_parts))

    result = "".join(out_parts)

    with _tracer.start_as_current_span("narration.second_person_swap") as span:
        span.set_attribute("swap_target_name", target_name)
        span.set_attribute("swap_count", total_count)

    return result, total_count
