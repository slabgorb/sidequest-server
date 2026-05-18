"""Pure fold of the trusted event log into the derived KnownFacts ledger.

Read-only forensic reconstruction. No DB, no I/O, no fabrication: a fact
appears in ``FoldResult.derived`` only if some event explicitly carried it
as a footnote.

**Why footnotes, not ``state_delta``** (verified against every real save
2026-05-18): recorded ``events`` rows are only ``NARRATION`` /
``SCRAPBOOK_ENTRY``; NARRATION payloads are ``{text, footnotes,
_visibility}`` and carry **no** ``state_delta`` key. Authoritative
mechanical state lives in the single ``game_state`` snapshot (the "Final
stored snapshot" panel); the per-player rendered view lives in
``projection_cache`` (the "projection lens" panel). The only real
*per-turn, derived-not-stored* signal in the event log is the footnote /
KnownFacts stream (ADR-100) — the narrator's accumulating working memory,
which is precisely how a long game stays coherent. Folding it by
``fact_id`` answers "what had the narrator established by round N, and on
which turns?" — the genuine autopsy distinct from the stored snapshot.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

from sidequest.game.event_log import EventRow

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DerivedField:
    """One reconstructed KnownFact and its provenance.

    ``value`` is the latest (highest-seq) ``{summary, category}`` for the
    fact; ``source_seqs`` is every event seq that asserted or restated it,
    in seq order.
    """

    value: object
    source_seqs: tuple[int, ...]


@dataclass(frozen=True)
class FoldResult:
    """Outcome of folding an ordered event slice. ``derived`` is keyed by
    ``fact_id`` (the fact's stable identity)."""

    derived: dict[str, DerivedField] = field(default_factory=dict)
    unparseable_seqs: tuple[int, ...] = ()


def fold_known_facts(events: list[EventRow]) -> FoldResult:
    """Fold events (any order) into the derived KnownFacts ledger.

    Events are sorted by ``seq`` internally. Folding rules:

    - A payload that fails JSON parsing, or parses to a non-dict, is
      skipped *loudly* (logged + recorded in ``unparseable_seqs``), never
      silently dropped.
    - An event with no ``footnotes`` list (SCRAPBOOK_ENTRY, footnote-less
      NARRATION) contributes nothing — that is not an error.
    - A footnote with no string ``fact_id`` has no stable identity; it is
      skipped *loudly* (``forensic_fold.malformed_footnote``) but its
      well-formed siblings in the same event still fold (No-Silent-
      Fallbacks: log, don't poison the whole event).
    - Repeated ``fact_id``: ``source_seqs`` accumulates every seq in order;
      ``value`` is the highest-seq assertion's ``{summary, category}``.
    """
    derived: dict[str, DerivedField] = {}
    unparseable: list[int] = []
    for ev in sorted(events, key=lambda e: e.seq):
        try:
            payload = json.loads(ev.payload_json)
        except (json.JSONDecodeError, TypeError):
            logger.warning("forensic_fold.unparseable_payload seq=%s", ev.seq)
            unparseable.append(ev.seq)
            continue
        if not isinstance(payload, dict):
            logger.warning("forensic_fold.non_dict_payload seq=%s", ev.seq)
            unparseable.append(ev.seq)
            continue
        footnotes = payload.get("footnotes")
        if not isinstance(footnotes, list):
            continue
        for fn in footnotes:
            if not isinstance(fn, dict):
                logger.warning("forensic_fold.malformed_footnote seq=%s", ev.seq)
                continue
            fact_id = fn.get("fact_id")
            if not isinstance(fact_id, str) or not fact_id:
                logger.warning("forensic_fold.malformed_footnote seq=%s", ev.seq)
                continue
            prev = derived.get(fact_id)
            seqs = (*prev.source_seqs, ev.seq) if prev else (ev.seq,)
            derived[fact_id] = DerivedField(
                value={"summary": fn.get("summary"), "category": fn.get("category")},
                source_seqs=seqs,
            )
    return FoldResult(derived=derived, unparseable_seqs=tuple(unparseable))


@dataclass(frozen=True)
class TelemetryRow:
    """One folded turn_telemetry row, ready for the forensics lane."""

    seq: int
    component: str
    event_type: str
    ts: str
    fields: dict


@dataclass(frozen=True)
class TelemetryFold:
    """Read-time curation of a round's turn_telemetry rows.

    ``rows`` are the parseable rows in seq order; ``by_component`` is
    component -> {event_type -> count}; ``unparseable_seqs`` records the
    loud-skipped rows (same contract as ``FoldResult.unparseable_seqs``)."""

    rows: tuple[TelemetryRow, ...] = ()
    by_component: dict = field(default_factory=dict)
    total: int = 0
    unparseable_seqs: tuple[int, ...] = ()


def fold_turn_telemetry(raw_rows: list) -> TelemetryFold:
    """Fold raw turn_telemetry rows (any order) into the forensics view.

    Pure, no I/O, never raises (mirrors ``fold_known_facts``):

    - A row that parses but fails (bad JSON, or a non-dict payload) is
      loud-logged and its int ``seq`` is recorded in ``unparseable_seqs``,
      never silently dropped.
    - A row so malformed it has no usable int ``seq`` (missing/None
      ``seq``) is loud-logged and skipped; it is NOT added to
      ``unparseable_seqs`` (there is no seq to record) — it still never
      raises and never crashes the page.
    - Output rows are sorted by ``seq``; ``by_component`` counts events
      grouped component -> event_type.
    """
    folded: list[TelemetryRow] = []
    unparseable: list[int] = []
    by_component: dict[str, dict[str, int]] = {}

    def _key(row) -> int:
        try:
            return int(row.get("seq"))
        except (TypeError, ValueError, AttributeError):
            return -1

    for row in sorted(raw_rows, key=_key):
        try:
            seq = int(row["seq"])
            component = str(row.get("component") or "")
            event_type = str(row.get("event_type") or "")
            ts = str(row.get("ts") or "")
            raw_payload = row.get("payload_json")
        except (KeyError, TypeError, AttributeError):
            seq_val = row.get("seq") if hasattr(row, "get") else None
            logger.warning("forensic_fold.telemetry_unparseable_payload seq=%s", seq_val)
            if isinstance(seq_val, int):
                unparseable.append(seq_val)
            continue
        try:
            payload = json.loads(raw_payload)
        except (json.JSONDecodeError, TypeError):
            logger.warning("forensic_fold.telemetry_unparseable_payload seq=%s", seq)
            unparseable.append(seq)
            continue
        if not isinstance(payload, dict):
            logger.warning("forensic_fold.telemetry_non_dict_payload seq=%s", seq)
            unparseable.append(seq)
            continue
        folded.append(
            TelemetryRow(
                seq=seq,
                component=component,
                event_type=event_type,
                ts=ts,
                fields=payload,
            )
        )
        by_component.setdefault(component, {})
        by_component[component][event_type] = by_component[component].get(event_type, 0) + 1

    return TelemetryFold(
        rows=tuple(folded),
        by_component=by_component,
        total=len(folded),
        unparseable_seqs=tuple(unparseable),
    )


# ---- Phase 2: mechanical-state census fold (mirrors fold_turn_telemetry) ----


@dataclass(frozen=True)
class PcMechanicalDiff:
    """One seated PC's mechanical state for a round.

    kind: 'baseline' (first census for this PC — absolute, no deltas),
    'static' (census fired, nothing changed), or 'moved' (typed deltas)."""

    player_id: str
    character_name: str
    seat: int
    kind: str
    deltas: tuple[tuple[str, str], ...] = ()
    absolute: dict = field(default_factory=dict)


@dataclass(frozen=True)
class MechanicalFold:
    """Read-time curation of a round's mechanical census.

    state: 'absent' (no census rows this round — save predates Phase 2),
    'static' (all PCs static), or 'moved' (>=1 PC moved/baseline)."""

    state: str = "absent"
    pcs: tuple[PcMechanicalDiff, ...] = ()
    trope: dict | None = None
    unparseable_seqs: tuple[int, ...] = ()


def _mech_rows(raw_rows: list, want_type: str):
    """Parse mechanical rows of one event_type; loud-skip bad ones.
    Returns (parsed_payloads, unparseable_seqs) — same contract as
    fold_turn_telemetry's loud-skip (logged + recorded, never silent)."""
    parsed: list[dict] = []
    unparseable: list[int] = []

    def _key(row) -> int:
        try:
            return int(row.get("seq"))
        except (TypeError, ValueError, AttributeError):
            return -1

    for row in sorted(raw_rows or [], key=_key):
        try:
            seq = int(row["seq"])
            if str(row.get("event_type") or "") != want_type:
                continue
            payload = json.loads(row.get("payload_json"))
        except (KeyError, TypeError, AttributeError, ValueError, json.JSONDecodeError):
            seq_val = row.get("seq") if hasattr(row, "get") else None
            logger.warning("forensic_fold.mechanical_unparseable_payload seq=%s", seq_val)
            if isinstance(seq_val, int):
                unparseable.append(seq_val)
            continue
        if not isinstance(payload, dict):
            logger.warning("forensic_fold.mechanical_non_dict_payload seq=%s", seq)
            unparseable.append(seq)
            continue
        parsed.append(payload)
    return parsed, unparseable


def _esc_num(n) -> str:
    try:
        return f"{int(n):+d}"
    except (TypeError, ValueError):
        return "?"


def _pc_deltas(cur: dict, prior: dict | None) -> tuple[tuple[str, str], ...]:
    """Typed consecutive diff. prior None -> caller renders baseline."""
    if prior is None:
        return ()
    out: list[tuple[str, str]] = []
    if cur.get("location") != prior.get("location"):
        out.append(("location", f"{prior.get('location')} → {cur.get('location')}"))
    ce, pe = cur.get("edge") or {}, prior.get("edge") or {}
    if ce.get("current") != pe.get("current"):
        try:
            d = int(ce.get("current")) - int(pe.get("current"))
            sign = "−" if d < 0 else "+"
            out.append(("edge", f"{pe.get('current')}→{ce.get('current')} ({sign}{abs(d)})"))
        except (TypeError, ValueError):
            out.append(("edge", f"{pe.get('current')}→{ce.get('current')}"))
    if cur.get("xp") != prior.get("xp"):
        try:
            out.append(("xp", _esc_num(int(cur.get("xp")) - int(prior.get("xp")))))
        except (TypeError, ValueError):
            out.append(("xp", f"{prior.get('xp')}→{cur.get('xp')}"))
    if cur.get("level") != prior.get("level"):
        out.append(("level", f"{prior.get('level')}→{cur.get('level')}"))
    # inventory: set-diff the aggregated digests by item name
    cmap = {
        i["item"]: i["qty"]
        for i in cur.get("inventory") or []
        if isinstance(i, dict) and "item" in i
    }
    pmap = {
        i["item"]: i["qty"]
        for i in prior.get("inventory") or []
        if isinstance(i, dict) and "item" in i
    }
    inv_bits: list[str] = []
    for item in sorted(set(cmap) | set(pmap)):
        cq, pq = cmap.get(item, 0), pmap.get(item, 0)
        if cq == pq:
            continue
        if pq == 0:
            inv_bits.append(f"+{item}" + (f"×{cq}" if cq != 1 else ""))
        elif cq == 0:
            inv_bits.append(f"−{item}×{pq}")
        else:
            inv_bits.append(f"{item}×{pq}→×{cq}")
    if inv_bits:
        out.append(("inventory", ", ".join(inv_bits)))
    ca = {a for a in (cur.get("acquired_advancements") or []) if isinstance(a, str)}
    pa = {a for a in (prior.get("acquired_advancements") or []) if isinstance(a, str)}
    if ca - pa:
        out.append(("advancements", ", ".join("+" + a for a in sorted(ca - pa))))
    return tuple(out)


def fold_mechanical_census(current_rows: list, prior_rows: list) -> MechanicalFold:
    """Pure, no I/O, never raises (mirrors fold_turn_telemetry).

    current_rows = this round's component='mechanical' rows; prior_rows =
    the previous CENSUS round's rows (read path supplies both). Per PC:
    no prior -> baseline; prior == current -> static; else moved with
    typed deltas. Round state: absent (no current rows) / static (all
    PCs static) / moved (any moved or baseline)."""
    cur_pc, u1 = _mech_rows(current_rows, "census")
    pri_pc, u2 = _mech_rows(prior_rows, "census")
    cur_tr, u3 = _mech_rows(current_rows, "trope_census")
    pri_tr, u4 = _mech_rows(prior_rows, "trope_census")
    unparseable = tuple(u1 + u2 + u3 + u4)

    if not cur_pc and not cur_tr:
        return MechanicalFold(state="absent", unparseable_seqs=unparseable)

    prior_by_pid = {p.get("player_id"): p for p in pri_pc}
    pcs: list[PcMechanicalDiff] = []
    any_moved = False
    for c in cur_pc:
        pid = c.get("player_id")
        prior = prior_by_pid.get(pid)
        if prior is None:
            kind, deltas = "baseline", ()
            any_moved = True
        else:
            deltas = _pc_deltas(c, prior)
            kind = "moved" if deltas else "static"
            any_moved = any_moved or bool(deltas)
        pcs.append(
            PcMechanicalDiff(
                player_id=str(pid),
                character_name=str(c.get("character_name") or ""),
                seat=c.get("seat") if isinstance(c.get("seat"), int) else -1,
                kind=kind,
                deltas=deltas,
                absolute={
                    k: c.get(k)
                    for k in (
                        "edge",
                        "location",
                        "inventory",
                        "xp",
                        "level",
                        "acquired_advancements",
                        "down",
                        "statuses",
                        "gold",
                        "chassis_room",
                    )
                },
            )
        )

    trope = None
    if cur_tr:
        ct = cur_tr[-1]
        pt = pri_tr[-1] if pri_tr else None
        cur_ids = {t.get("id"): t for t in ct.get("active_tropes") or [] if isinstance(t, dict)}
        pri_ids = (
            {t.get("id"): t for t in (pt or {}).get("active_tropes") or [] if isinstance(t, dict)}
            if pt
            else {}
        )
        bits: list[str] = []
        for tid in sorted(cur_ids, key=lambda x: (x is None, str(x))):
            ctp = cur_ids[tid]
            ptp = pri_ids.get(tid)
            if ptp is None:
                bits.append(f"{tid} → {ctp.get('status')} p={ctp.get('progress')}")
            elif ptp.get("progress") != ctp.get("progress") or ptp.get("status") != ctp.get(
                "status"
            ):
                bits.append(f"{tid} {ptp.get('progress')}→{ctp.get('progress')}")
        trope = {
            "summary": "; ".join(bits) if bits else "· no trope change",
            "kind": "moved" if (pt is None or bits) else "static",
            "turns_since_meaningful": ct.get("turns_since_meaningful"),
            "total_beats_fired": ct.get("total_beats_fired"),
        }

    state = "moved" if any_moved or (trope and trope["kind"] == "moved") else "static"
    return MechanicalFold(
        state=state,
        pcs=tuple(pcs),
        trope=trope,
        unparseable_seqs=unparseable,
    )


def fold_mechanical_strip(all_rows: list) -> list[dict]:
    """Whole-save per-round tri-state for the macro strip. One pass,
    computed once at save-select (mirrors the P1.1 'needs the per-round
    fold' reservation). Pure, never raises. Returns
    [{round, state}] in round order; absent rounds simply do not appear."""
    census, _ = _mech_rows(all_rows, "census")
    tropes, _ = _mech_rows(all_rows, "trope_census")
    by_round: dict[int, list[dict]] = {}
    for p in census:
        r = p.get("round")
        if isinstance(r, int):
            by_round.setdefault(r, []).append(p)
    tr_by_round: dict[int, list[dict]] = {}
    for p in tropes:
        r = p.get("round")
        if isinstance(r, int):
            tr_by_round.setdefault(r, []).append(p)

    out: list[dict] = []
    prev_pc: dict[str, dict] = {}
    prev_tr: dict | None = None
    for rnd in sorted(set(by_round) | set(tr_by_round)):
        moved = False
        for c in by_round.get(rnd, []):
            pid = c.get("player_id")
            prior = prev_pc.get(pid)
            if prior is None or _pc_deltas(c, prior):
                moved = True
            prev_pc[pid] = c
        tlist = tr_by_round.get(rnd, [])
        if tlist:
            ct = tlist[-1]
            if prev_tr is None or json.dumps(ct.get("active_tropes"), sort_keys=True) != json.dumps(
                prev_tr.get("active_tropes"), sort_keys=True
            ):
                moved = True
            prev_tr = ct
        out.append({"round": rnd, "state": "moved" if moved else "static"})
    return out
