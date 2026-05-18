"""Read-only DB assembly for the save-forensics page.

Mirrors the module-level ``query_encounter_events(store)`` precedent:
plain functions over an open SQLite connection. Never writes, never
checkpoints (respects the WAL/save-clobber hazard).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

from sidequest.game.event_log import EventRow
from sidequest.game.forensic_fold import fold_state_deltas

logger = logging.getLogger(__name__)


def _ro_connect(db_path: Path) -> sqlite3.Connection:
    """Strictly read-only: no schema init, no migration, no WAL flip.

    ``SqliteStore.open`` writes on construction (schema + migrations +
    commit + journal_mode=WAL) — forbidden here per the save-clobber hazard.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    # NOTE: opening a WAL-mode save read-only still materializes a harmless
    # save.db-shm (SQLite read-side shared-memory index) — NOT a main-db
    # write; list_saves' read-only/byte-identity contract is unaffected.
    conn.row_factory = sqlite3.Row
    return conn


def list_saves(save_dir: Path) -> list[dict]:
    """Enumerate ``<save_dir>/games/<slug>/save.db`` files.

    Broken/meta-less DBs are skipped *loudly* (logged WARNING), never
    silently. Sorted newest-first by save-file mtime.
    """
    games_root = Path(save_dir) / "games"
    out: list[dict] = []
    if not games_root.exists():
        return out
    for slug_dir in sorted(games_root.iterdir()):
        if not slug_dir.is_dir():
            continue
        db_file = slug_dir / "save.db"
        if not db_file.is_file():
            continue
        conn: sqlite3.Connection | None = None
        try:
            conn = _ro_connect(db_file)
            row = conn.execute(
                "SELECT genre_slug, world_slug, created_at, last_played "
                "FROM session_meta WHERE id = 1"
            ).fetchone()
        except Exception as exc:  # noqa: BLE001 — best-effort enumeration
            logger.warning("forensic_query.open_failed slug=%s err=%s", slug_dir.name, exc)
            continue
        finally:
            if conn is not None:
                conn.close()
        if row is None:
            logger.warning("forensic_query.no_meta slug=%s", slug_dir.name)
            continue
        out.append(
            {
                "slug": slug_dir.name,
                "genre": row["genre_slug"],
                "world": row["world_slug"],
                "created_at": row["created_at"],
                "last_played": row["last_played"],
                "last_activity_ts": int(db_file.stat().st_mtime * 1000),
            }
        )
    out.sort(key=lambda r: r["last_activity_ts"], reverse=True)
    return out


def _round_boundaries(conn: sqlite3.Connection) -> list[tuple[int, str]]:
    """Ordered (round_number, min_created_at) per round in narrative_log."""
    rows = conn.execute(
        "SELECT round_number, MIN(created_at) AS first_ts "
        "FROM narrative_log GROUP BY round_number ORDER BY round_number"
    ).fetchall()
    return [(r["round_number"], r["first_ts"]) for r in rows]


# events.created_at is Python .isoformat() ('T' sep, microseconds, tz);
# narrative_log.created_at is sqlite datetime('now') 'YYYY-MM-DD HH:MM:SS'
# (space, second precision, no tz). Normalize the EVENT side to the
# narrative side's exact shape so the lexical bucket comparison is correct
# (Spike Findings F3/F4). Second granularity suffices — real round
# boundaries are minutes apart and seq-range contiguity is preserved.
_NORM_EV_TS = "substr(replace(created_at, 'T', ' '), 1, 19)"


def _events_for_round(
    conn: sqlite3.Connection, lo_ts: str, hi_ts: str | None, *, first_round: bool
):
    """Events whose NORMALIZED created_at is in [lo_ts, hi_ts). The first
    round also sweeps any events that predate the first narrative row."""
    if first_round and hi_ts is not None:
        sql = f"SELECT seq, kind, created_at FROM events WHERE {_NORM_EV_TS} < ? ORDER BY seq"
        return conn.execute(sql, (hi_ts,)).fetchall()
    if first_round and hi_ts is None:
        return conn.execute(
            "SELECT seq, kind, created_at FROM events ORDER BY seq"
        ).fetchall()
    if hi_ts is None:
        sql = f"SELECT seq, kind, created_at FROM events WHERE {_NORM_EV_TS} >= ? ORDER BY seq"
        return conn.execute(sql, (lo_ts,)).fetchall()
    sql = (
        f"SELECT seq, kind, created_at FROM events "
        f"WHERE {_NORM_EV_TS} >= ? AND {_NORM_EV_TS} < ? ORDER BY seq"
    )
    return conn.execute(sql, (lo_ts, hi_ts)).fetchall()


def build_timeline(conn: sqlite3.Connection) -> list[dict]:
    """One entry per narrative round, with its event seq-range + summary.

    Join: normalized-timestamp bucketing (Spike Findings F4) — events lack
    a round column and use a different created_at format than narrative_log.
    Read-only: caller supplies a read-only connection (D4).
    """
    bounds = _round_boundaries(conn)
    timeline: list[dict] = []
    for idx, (rnd, lo_ts) in enumerate(bounds):
        hi_ts = bounds[idx + 1][1] if idx + 1 < len(bounds) else None
        evs = _events_for_round(conn, lo_ts, hi_ts, first_round=(idx == 0))
        kind_counts: dict[str, int] = {}
        for e in evs:
            kind_counts[e["kind"]] = kind_counts.get(e["kind"], 0) + 1
        authors = [
            r["author"]
            for r in conn.execute(
                "SELECT DISTINCT author FROM narrative_log "
                "WHERE round_number = ? ORDER BY author",
                (rnd,),
            ).fetchall()
        ]
        timeline.append(
            {
                "round": rnd,
                "seq_start": evs[0]["seq"] if evs else None,
                "seq_end": evs[-1]["seq"] if evs else None,
                "event_kind_counts": kind_counts,
                "narrative_authors": authors,
                "ts": lo_ts,
            }
        )
    return timeline


def _timeline_entry(conn: sqlite3.Connection, round_number: int) -> dict | None:
    for entry in build_timeline(conn):
        if entry["round"] == round_number:
            return entry
    return None


def _safe_json(raw: str | None):
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"__unparseable__": raw}


def _safe_json_list(raw: str | None) -> list:
    """Read-only display decode for list-typed stored columns. Never raises
    (forensics inspects corrupt saves); on null/parse-failure/non-list it
    logs LOUDLY (No-Silent-Fallbacks) and degrades to []."""
    if raw is None:
        return []
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        logger.warning("forensic_query.unparseable_json_list raw=%r", raw)
        return []
    if not isinstance(parsed, list):
        logger.warning("forensic_query.unparseable_json_list raw=%r", raw)
        return []
    return parsed


def build_turn_bundle(conn: sqlite3.Connection, round_number: int) -> dict:
    """Assemble every drill-down panel for one round.

    Truth tiers stay separate: ``narrative``/``events``/``projection``/
    ``scrapbook`` are verbatim DB rows; ``derived`` is the fold of every
    event up to and including this round's last seq, badged by the UI.
    Unknown round → empty bundle (lossy/best-effort, never raises).
    Read-only: caller supplies a read-only connection (D4).
    """
    entry = _timeline_entry(conn, round_number)

    narrative = [
        {"round": r["round_number"], "author": r["author"],
         "content": r["content"], "tags": _safe_json_list(r["tags"]),
         "created_at": r["created_at"]}
        for r in conn.execute(
            "SELECT round_number, author, content, tags, created_at "
            "FROM narrative_log WHERE round_number = ? ORDER BY id",
            (round_number,),
        ).fetchall()
    ]

    if entry is None or entry["seq_start"] is None:
        return {"round": round_number, "narrative": narrative, "events": [],
                "derived": {}, "projection": [], "scrapbook": [],
                "unparseable_seqs": []}

    seq_start, seq_end = entry["seq_start"], entry["seq_end"]
    raw_events = conn.execute(
        "SELECT seq, kind, payload_json, created_at FROM events "
        "WHERE seq >= ? AND seq <= ? ORDER BY seq",
        (seq_start, seq_end),
    ).fetchall()
    events = [
        {"seq": e["seq"], "kind": e["kind"],
         "payload": _safe_json(e["payload_json"]), "created_at": e["created_at"]}
        for e in raw_events
    ]

    fold_rows = conn.execute(
        "SELECT seq, kind, payload_json, created_at FROM events "
        "WHERE seq <= ? ORDER BY seq",
        (seq_end,),
    ).fetchall()
    fold = fold_state_deltas(
        [EventRow(seq=r["seq"], kind=r["kind"],
                  payload_json=r["payload_json"], created_at=r["created_at"])
         for r in fold_rows]
    )
    derived = {
        k: {"value": v.value, "source_seqs": list(v.source_seqs)}
        for k, v in fold.derived.items()
    }

    projection = [
        {"event_seq": p["event_seq"], "player_id": p["player_id"],
         "include": p["include"], "payload": _safe_json(p["payload_json"])}
        for p in conn.execute(
            "SELECT event_seq, player_id, include, payload_json "
            "FROM projection_cache WHERE event_seq >= ? AND event_seq <= ? "
            "ORDER BY event_seq, player_id",
            (seq_start, seq_end),
        ).fetchall()
    ]

    scrapbook = [
        {"scene_title": s["scene_title"], "scene_type": s["scene_type"],
         "location": s["location"], "image_url": s["image_url"],
         "narrative_excerpt": s["narrative_excerpt"],
         "world_facts": _safe_json_list(s["world_facts"]),
         "npcs_present": _safe_json_list(s["npcs_present"]),
         "render_status": s["render_status"]}
        for s in conn.execute(
            "SELECT scene_title, scene_type, location, image_url, "
            "narrative_excerpt, world_facts, npcs_present, render_status "
            "FROM scrapbook_entries WHERE turn_id = ? ORDER BY id",
            (round_number,),
        ).fetchall()
    ]

    return {"round": round_number, "narrative": narrative, "events": events,
            "derived": derived, "projection": projection,
            "scrapbook": scrapbook,
            "unparseable_seqs": list(fold.unparseable_seqs)}
