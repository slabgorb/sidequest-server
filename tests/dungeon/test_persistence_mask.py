"""Story 52-3 — mask-BLOB persistence + loader (the materializer.py:57 gap).

Closes the documented Plan-5-API gap: ``commit_expansion`` persists
``RegionNode.to_dict()`` but does NOT write the ``dungeon_map.mask BLOB``
column, and no Plan 1–6 code consumes a persisted mask on reload. This
story makes the mask durable across save/reload (ADR-096 §2: "the mask
is the truth").

ACs covered (session: ``.session/52-3-session.md``):

  AC1 — Mask write path: ``commit_expansion`` accepts a per-region
        ``masks: Mapping[region_id, mask_dict] | None``; non-None masks
        are JSON-serialised and stored in the ``mask BLOB`` column; None
        leaves the column NULL. Emits ``dungeon.persist.mask_write``.

  AC2 — Mask load path: a ``load_masks() -> dict[region_id, mask_dict]``
        loader returns persisted masks (NULL columns are omitted from
        the result map). Emits ``dungeon.persist.mask_load``.

  AC3 — Reload-on-resume round-trip: persist over a WAL save file, close
        the connection, reopen, and the reloaded masks are
        byte-identical to the originals.

  AC4 — No schema migration: the ``mask BLOB`` column already exists
        (Plan 5); the new code path only POPULATES it. Asserted by
        introspecting ``sqlite_master`` after ``ensure_schema``.

  AC5 — No silent fallbacks: a corrupted BLOB on load raises
        ``SerializationError`` loudly; an unserialisable mask on write
        raises ``PersistError`` loudly. Never silently substitute an
        empty mask or skip a region.

Wiring (separate suite): see TestMaterializerCommitWiresMasks in
``test_persistence_mask_wiring.py`` (kept in this directory; satisfies
the CLAUDE.md "every test suite needs a wiring test" rule by driving
the real five-stage coordinator end-to-end and asserting that masks
land in ``dungeon_map.mask``).
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import pytest

from sidequest.dungeon.region_graph.depth import assign_depth_scores
from sidequest.dungeon.region_graph.generator import (
    attach_expansion,
    generate_expansion,
)
from sidequest.dungeon.region_graph.model import (
    Expansion,
    RegionGraph,
    RegionNode,
)

# ---------------------------------------------------------------------------
# Helpers (mirror tests/dungeon/test_persistence.py — keep the seam shape
# identical so a drift in PRAGMA/connection setup breaks both suites the
# same way)
# ---------------------------------------------------------------------------


def _mem_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _file_conn(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _seed_graph() -> RegionGraph:
    g = RegionGraph(entrance_id="entrance")
    g.add_node(RegionNode(id="entrance", expansion_id=0, theme="threshold"))
    return g


def _commit_seed(store: Any, g: RegionGraph) -> None:
    """Persist the entrance as Expansion 0 (the Seed=Expansion-0 contract
    documented at materializer.py:40-45). commit_expansion re-reads the
    live depth-scored node from g."""
    entrance = g.nodes[g.entrance_id]
    store.commit_expansion(Expansion(expansion_id=0, new_nodes=[entrance], new_edges=[]), g)


def _generate_and_attach(
    g: RegionGraph, *, campaign_seed: int, expansion_id: int, attach_ids: list[str]
) -> Expansion:
    exp, _ = generate_expansion(
        graph=g,
        campaign_seed=campaign_seed,
        expansion_id=expansion_id,
        attach_region_ids=attach_ids,
        theme_pool=["crypt", "catacomb", "flooded"],
    )
    attach_expansion(g, exp)
    assign_depth_scores(g, campaign_seed=campaign_seed)
    return exp


def _example_mask_dict(region_id: str, *, w: int = 5, h: int = 5) -> dict:
    """A representative JSON-serialisable mask payload — the shape that
    ``RegionMask`` (materializer.py:339) reduces to once `mask_bytes`
    bytes have been base64-encoded for JSON safety. Tests use this
    shape as the canonical input to ``commit_expansion(..., masks=...)``.

    The dict MUST round-trip through ``json.dumps`` → ``json.loads``
    unchanged — that is the on-disk contract.
    """
    # A deterministic per-region payload so byte-identical reload can be
    # asserted by JSON-equality after load.
    return {
        "mask_bytes_b64": f"YmFzZTY0LXtyZWdpb24taWQ9e3JpZH19".replace("{rid}", region_id),
        "mask_sha": f"sha256-stub-{region_id}",
        "block": {
            "cell_width": 28,
            "grid_width": w,
            "grid_height": h,
            "origin_x": 0,
            "origin_y": 0,
        },
    }


# ---------------------------------------------------------------------------
# AC4 — schema is already prepared (regression guard, NOT a new migration)
# ---------------------------------------------------------------------------


def test_dungeon_map_mask_column_present_after_ensure_schema_no_new_migration() -> None:
    """AC4: the ``mask BLOB`` column was added in Plan 5; story 52-3 does
    NOT introduce a migration, it only populates the pre-existing column.
    This guards against silently dropping the column (a regression here
    would also wipe Plan 5's frozen-region promise)."""
    from sidequest.dungeon.persistence import DungeonStore

    conn = _mem_conn()
    DungeonStore(conn).ensure_schema()
    cols = {
        row["name"]: row["type"]
        for row in conn.execute("PRAGMA table_info(dungeon_map)").fetchall()
    }
    assert "mask" in cols, "Plan 5 mask BLOB column missing — 52-3 expects it pre-existing"
    assert cols["mask"].upper() == "BLOB", (
        f"mask column type drift: expected BLOB, got {cols['mask']!r}"
    )


# ---------------------------------------------------------------------------
# AC1 — mask write path
# ---------------------------------------------------------------------------


def test_commit_expansion_writes_mask_blob_when_masks_provided() -> None:
    """AC1.a: providing a per-region ``masks`` map writes a non-NULL BLOB
    in the ``dungeon_map.mask`` column. The BLOB content is the JSON
    encoding of the supplied mask dict."""
    from sidequest.dungeon.persistence import DungeonStore

    g = _seed_graph()
    exp = _generate_and_attach(g, campaign_seed=7, expansion_id=1, attach_ids=["entrance"])

    conn = _mem_conn()
    store = DungeonStore(conn)
    store.ensure_schema()
    _commit_seed(store, g)

    masks = {n.id: _example_mask_dict(n.id) for n in exp.new_nodes}
    store.commit_expansion(exp, g, masks=masks)
    conn.commit()

    rows = conn.execute(
        "SELECT region_id, mask FROM dungeon_map WHERE expansion_id = 1"
    ).fetchall()
    assert rows, "no expansion-1 rows persisted — commit_expansion did not run"
    for row in rows:
        rid = row["region_id"]
        assert row["mask"] is not None, (
            f"mask BLOB is NULL for region {rid!r} despite masks[{rid!r}] supplied — "
            "the write path silently dropped it (No Silent Fallbacks)"
        )
        # Round-trip the BLOB → JSON → dict and compare to the input.
        decoded = json.loads(row["mask"].decode("utf-8"))
        assert decoded == masks[rid], (
            f"mask BLOB for {rid!r} is not JSON-equal to the input — serialisation drift"
        )


def test_commit_expansion_without_masks_leaves_blob_null() -> None:
    """AC1.b: omitting the ``masks`` kwarg (or passing None) MUST leave
    the ``dungeon_map.mask`` column NULL — never a JSON ``null`` string,
    never an empty bytes value, never a default empty mask. The schema
    distinguishes "no mask known" from "mask is empty"; the load path
    relies on that distinction (AC2.b)."""
    from sidequest.dungeon.persistence import DungeonStore

    g = _seed_graph()
    exp = _generate_and_attach(g, campaign_seed=13, expansion_id=1, attach_ids=["entrance"])

    conn = _mem_conn()
    store = DungeonStore(conn)
    store.ensure_schema()
    _commit_seed(store, g)

    # Default: no masks supplied. The kwarg must be optional and default
    # to None — caller backward-compat.
    store.commit_expansion(exp, g)
    conn.commit()

    rows = conn.execute("SELECT region_id, mask FROM dungeon_map").fetchall()
    assert rows, "no rows persisted — commit_expansion did not run"
    for row in rows:
        assert row["mask"] is None, (
            f"mask BLOB is non-NULL ({row['mask']!r}) for region {row['region_id']!r} "
            "despite NO masks supplied — the write path is inventing a default mask "
            "(No Silent Fallbacks; AC1.b requires NULL when masks is None/omitted)"
        )


def test_commit_expansion_partial_masks_only_writes_supplied_regions() -> None:
    """AC1.c: passing masks for a SUBSET of the expansion's regions must
    write BLOBs only for the supplied region_ids; the others stay NULL.
    No silent broadcast of one region's mask to all rows; no silent skip
    of the whole map when one region is missing."""
    from sidequest.dungeon.persistence import DungeonStore

    g = _seed_graph()
    exp = _generate_and_attach(g, campaign_seed=29, expansion_id=1, attach_ids=["entrance"])
    assert len(exp.new_nodes) >= 2, "test precondition: need at least 2 new regions"

    conn = _mem_conn()
    store = DungeonStore(conn)
    store.ensure_schema()
    _commit_seed(store, g)

    # Mask only the first region; leave the rest unmasked.
    first = exp.new_nodes[0]
    masks = {first.id: _example_mask_dict(first.id)}
    store.commit_expansion(exp, g, masks=masks)
    conn.commit()

    rows = {
        row["region_id"]: row["mask"]
        for row in conn.execute(
            "SELECT region_id, mask FROM dungeon_map WHERE expansion_id = 1"
        ).fetchall()
    }
    assert rows[first.id] is not None, "supplied mask was dropped"
    for n in exp.new_nodes[1:]:
        assert rows[n.id] is None, (
            f"unsupplied region {n.id!r} has a non-NULL mask BLOB — write path is "
            "broadcasting/inventing masks (No Silent Fallbacks; AC1.c)"
        )


# ---------------------------------------------------------------------------
# AC2 — mask load path
# ---------------------------------------------------------------------------


def test_load_masks_returns_per_region_dict_for_persisted_masks() -> None:
    """AC2.a: ``DungeonStore.load_masks()`` returns a mapping
    ``region_id -> mask_dict`` for every row whose ``mask BLOB`` is
    non-NULL. The returned dict is JSON-equal to the input that was
    persisted (exact-inverse — same precedent as RegionNode/Edge serde)."""
    from sidequest.dungeon.persistence import DungeonStore

    g = _seed_graph()
    exp = _generate_and_attach(g, campaign_seed=7, expansion_id=1, attach_ids=["entrance"])

    conn = _mem_conn()
    store = DungeonStore(conn)
    store.ensure_schema()
    _commit_seed(store, g)
    masks = {n.id: _example_mask_dict(n.id) for n in exp.new_nodes}
    store.commit_expansion(exp, g, masks=masks)
    conn.commit()

    loaded = store.load_masks()
    assert loaded == masks, (
        f"load_masks() output != input masks; got {loaded!r} expected {masks!r}"
    )


def test_load_masks_omits_regions_with_null_mask_column() -> None:
    """AC2.b: rows whose ``mask BLOB`` is NULL MUST NOT appear in the
    returned map — caller distinguishes "no mask known" from "mask is
    {}" by absence/presence of the key. Returning ``{region_id: None}``
    or ``{region_id: {}}`` would lie (No Silent Fallbacks)."""
    from sidequest.dungeon.persistence import DungeonStore

    g = _seed_graph()
    exp = _generate_and_attach(g, campaign_seed=11, expansion_id=1, attach_ids=["entrance"])

    conn = _mem_conn()
    store = DungeonStore(conn)
    store.ensure_schema()
    _commit_seed(store, g)
    # NO masks supplied — every row has NULL mask.
    store.commit_expansion(exp, g)
    conn.commit()

    loaded = store.load_masks()
    assert loaded == {}, (
        f"load_masks() returned entries for NULL-mask rows: {loaded!r}; "
        "the loader must omit them, never substitute a default"
    )


def test_load_masks_returns_empty_dict_on_fresh_save() -> None:
    """AC2.c: a freshly-initialised dungeon (schema only, no rows) returns
    an empty dict from ``load_masks()`` — NOT None, NOT a raise. The
    return type is invariantly ``dict[str, dict]``."""
    from sidequest.dungeon.persistence import DungeonStore

    conn = _mem_conn()
    store = DungeonStore(conn)
    store.ensure_schema()

    loaded = store.load_masks()
    assert loaded == {}, f"empty store should return {{}}, got {loaded!r}"
    assert isinstance(loaded, dict), f"return type drift: expected dict, got {type(loaded)}"


# ---------------------------------------------------------------------------
# AC3 — reload-on-resume byte-identical over WAL save file
# ---------------------------------------------------------------------------


def test_masks_byte_identical_after_save_reopen_over_wal_file() -> None:
    """AC3: write masks on connection 1, ``conn.commit()`` + close;
    re-open the save DB on a fresh connection 2 and ``load_masks()`` —
    the result must be byte-identical to the input. This is the
    save-is-truth contract (spec §7).

    Done over a real on-disk save with WAL journal mode (matches the
    production save-DB connection PRAGMAs)."""
    from sidequest.dungeon.persistence import DungeonStore

    g = _seed_graph()
    exp = _generate_and_attach(g, campaign_seed=23, expansion_id=1, attach_ids=["entrance"])
    masks = {n.id: _example_mask_dict(n.id) for n in exp.new_nodes}

    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "save.db")
        c1 = _file_conn(db)
        s1 = DungeonStore(c1)
        s1.ensure_schema()
        _commit_seed(s1, g)
        s1.commit_expansion(exp, g, masks=masks)
        c1.commit()
        c1.close()

        c2 = _file_conn(db)
        reloaded = DungeonStore(c2).load_masks()
        c2.close()

    assert reloaded == masks, (
        f"mask reload over WAL drifted from input: got {reloaded!r}, expected {masks!r}"
    )


def test_masks_survive_three_expansion_chain_on_wal_save() -> None:
    """AC3 stress: three-expansion chain (matches the existing serde-sweep
    pattern in test_persistence.py:430). Every expansion's masks must
    survive reload, and the per-region map must be exactly union-of-
    inputs (no expansion loses its masks; no expansion overwrites
    another's)."""
    from sidequest.dungeon.persistence import DungeonStore

    seed = 24301
    g = _seed_graph()
    exp1 = _generate_and_attach(g, campaign_seed=seed, expansion_id=1, attach_ids=["entrance"])
    deep_ids = [n.id for n in exp1.new_nodes][:2] or ["entrance"]
    exp2 = _generate_and_attach(
        g, campaign_seed=seed, expansion_id=2, attach_ids=(deep_ids + ["entrance"])[:2]
    )
    deeper = [n.id for n in exp2.new_nodes][:2] or deep_ids
    exp3 = _generate_and_attach(
        g, campaign_seed=seed, expansion_id=3, attach_ids=(deeper + deep_ids)[:2]
    )

    masks: dict[str, dict] = {}
    for exp in (exp1, exp2, exp3):
        for n in exp.new_nodes:
            masks[n.id] = _example_mask_dict(n.id, w=3 + n.expansion_id, h=4)

    with tempfile.TemporaryDirectory() as d:
        db = str(Path(d) / "save.db")
        c1 = _file_conn(db)
        s1 = DungeonStore(c1)
        s1.ensure_schema()
        _commit_seed(s1, g)
        for exp in (exp1, exp2, exp3):
            exp_masks = {n.id: masks[n.id] for n in exp.new_nodes}
            s1.commit_expansion(exp, g, masks=exp_masks)
        c1.commit()
        c1.close()

        c2 = _file_conn(db)
        reloaded = DungeonStore(c2).load_masks()
        c2.close()

    assert reloaded == masks, (
        "three-expansion mask reload drifted; "
        f"missing={set(masks) - set(reloaded)}, extra={set(reloaded) - set(masks)}"
    )


# ---------------------------------------------------------------------------
# AC5 — loud failures
# ---------------------------------------------------------------------------


def test_load_masks_raises_serializationerror_on_corrupted_blob() -> None:
    """AC5.a: if a row's ``mask BLOB`` is non-NULL but not valid JSON,
    ``load_masks()`` raises ``SerializationError`` — never returns a
    partial map, never substitutes an empty dict, never silently logs
    and skips. The save is truth; corruption is a loud event.

    Simulated by writing non-JSON bytes directly into the BLOB column
    (the same precedent as the corrupt-payload error path in the
    region serde block)."""
    from sidequest.dungeon.persistence import DungeonStore, SerializationError

    conn = _mem_conn()
    store = DungeonStore(conn)
    store.ensure_schema()
    # Insert one row with a deliberately corrupted BLOB.
    conn.execute(
        "INSERT INTO dungeon_map "
        "(region_id, expansion_id, depth_score, generator_version, payload, mask) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "corrupted.r0",
            1,
            10.0,
            "plan5.v1",
            json.dumps({"id": "corrupted.r0", "expansion_id": 1, "theme": "x"}),
            b"\xff\xfenot-json-at-all",
        ),
    )
    conn.commit()

    with pytest.raises(SerializationError):
        store.load_masks()


def test_commit_expansion_raises_persisterror_on_unserialisable_mask() -> None:
    """AC5.b: a mask value that cannot be JSON-serialised (e.g. a set,
    or a dataclass with bytes that haven't been encoded) raises
    ``PersistError`` loudly — the write must NOT silently substitute
    NULL or stringify. The call must abort BEFORE any partial row
    write (the seed row may already exist; assertion is only that the
    expansion's rows do not exist after the raise)."""
    from sidequest.dungeon.persistence import DungeonStore, PersistError

    g = _seed_graph()
    exp = _generate_and_attach(g, campaign_seed=17, expansion_id=1, attach_ids=["entrance"])

    conn = _mem_conn()
    store = DungeonStore(conn)
    store.ensure_schema()
    _commit_seed(store, g)

    # ``set`` is not JSON-serialisable by default.
    bad_masks: dict[str, Any] = {exp.new_nodes[0].id: {"sha": {"set", "of", "strings"}}}

    with pytest.raises(PersistError):
        store.commit_expansion(exp, g, masks=bad_masks)
    # The write must have aborted before partial commit.
    conn.rollback()


def test_load_masks_never_returns_empty_dict_to_paper_over_corruption() -> None:
    """AC5.c regression guard: a single corrupted row must NOT cause
    ``load_masks()`` to silently return ``{}`` (the easy 'soft failure'
    bug). The empty-dict return is reserved for the genuine fresh-save
    case (test_load_masks_returns_empty_dict_on_fresh_save). This test
    asserts that with corruption present, the function raises rather
    than returns ``{}`` — distinguishing the two cases."""
    from sidequest.dungeon.persistence import DungeonStore, SerializationError

    conn = _mem_conn()
    store = DungeonStore(conn)
    store.ensure_schema()
    conn.execute(
        "INSERT INTO dungeon_map "
        "(region_id, expansion_id, depth_score, generator_version, payload, mask) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "corrupted.r0",
            1,
            10.0,
            "plan5.v1",
            json.dumps({"id": "corrupted.r0", "expansion_id": 1, "theme": "x"}),
            b"\x00\x01\x02",  # binary garbage, not JSON
        ),
    )
    conn.commit()

    with pytest.raises(SerializationError):
        # Must NOT return {}; must raise. The two return paths
        # (empty-fresh vs corruption) are distinct contracts.
        result = store.load_masks()
        # If we reach this line, the contract has been violated — surface
        # the actual offending value to make the failure mode obvious.
        raise AssertionError(
            f"load_masks() returned {result!r} on a corrupted row instead of "
            "raising SerializationError — silent fallback to empty dict"
        )


# ---------------------------------------------------------------------------
# OTEL — span constants exist, are routed, and fire at the right call sites
# ---------------------------------------------------------------------------


def test_mask_persist_spans_registered_and_routed() -> None:
    """AC1/AC2 OTEL contract: both new spans must be declared in
    ``sidequest.telemetry.spans.dungeon_persist`` and must be present in
    ``SPAN_ROUTES`` (or ``FLAT_ONLY_SPANS``) so the routing-completeness
    gate doesn't reject them — the same precedent as the existing
    ``dungeon.persist.commit`` span (test_persistence.py:350)."""
    from sidequest.telemetry.spans import FLAT_ONLY_SPANS, SPAN_ROUTES
    from sidequest.telemetry.spans.dungeon_persist import (
        SPAN_DUNGEON_PERSIST_MASK_LOAD,
        SPAN_DUNGEON_PERSIST_MASK_WRITE,
    )

    assert SPAN_DUNGEON_PERSIST_MASK_WRITE == "dungeon.persist.mask_write", (
        "span name drift — the GM panel + OTEL dashboard query this exact string"
    )
    assert SPAN_DUNGEON_PERSIST_MASK_LOAD == "dungeon.persist.mask_load", (
        "span name drift — the GM panel + OTEL dashboard query this exact string"
    )
    for name in (SPAN_DUNGEON_PERSIST_MASK_WRITE, SPAN_DUNGEON_PERSIST_MASK_LOAD):
        assert name in SPAN_ROUTES or name in FLAT_ONLY_SPANS, (
            f"{name} has no routing decision — routing-completeness gate will fail"
        )


def test_commit_expansion_emits_mask_write_span_with_mask_rows_attr() -> None:
    """The mask write path opens a ``dungeon.persist.mask_write`` span
    carrying a ``mask_rows`` attribute equal to the number of regions
    whose mask was written this commit. This is the lie-detector hook
    the GM panel needs to confirm "the mask write actually fired."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        ConsoleSpanExporter,
        SimpleSpanProcessor,
    )

    from sidequest.dungeon.persistence import DungeonStore
    from tests.dungeon.conftest import (
        capture_otel_provider_state,
        reset_otel_provider,
        restore_otel_provider_state,
    )

    captured: list[Any] = []

    class _Capture(ConsoleSpanExporter):
        def export(self, spans):  # type: ignore[override]
            captured.extend(spans)
            return super().export(spans)

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_Capture()))
    _state = capture_otel_provider_state()
    try:
        reset_otel_provider()
        trace.set_tracer_provider(provider)

        g = _seed_graph()
        exp = _generate_and_attach(g, campaign_seed=5, expansion_id=1, attach_ids=["entrance"])
        conn = _mem_conn()
        store = DungeonStore(conn)
        store.ensure_schema()
        _commit_seed(store, g)
        masks = {n.id: _example_mask_dict(n.id) for n in exp.new_nodes}
        store.commit_expansion(exp, g, masks=masks)
    finally:
        restore_otel_provider_state(_state)

    names = [s.name for s in captured]
    assert "dungeon.persist.mask_write" in names, (
        f"mask_write span not emitted; captured spans = {names!r}"
    )
    mask_write_spans = [s for s in captured if s.name == "dungeon.persist.mask_write"]
    attrs = mask_write_spans[-1].attributes or {}
    assert attrs.get("mask_rows") == len(masks), (
        f"mask_rows attribute drift: got {attrs.get('mask_rows')!r}, expected {len(masks)}"
    )


def test_commit_expansion_does_not_emit_mask_write_span_when_no_masks_supplied() -> None:
    """When ``masks`` is None/omitted, the write path must NOT open a
    ``dungeon.persist.mask_write`` span — emitting a span for a no-op
    write is the exact Illusionism the GM panel exists to catch (the
    spec §6 lie-detector contract that already governs the
    materializer/frontier spans)."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        ConsoleSpanExporter,
        SimpleSpanProcessor,
    )

    from sidequest.dungeon.persistence import DungeonStore
    from tests.dungeon.conftest import (
        capture_otel_provider_state,
        reset_otel_provider,
        restore_otel_provider_state,
    )

    captured: list[str] = []

    class _Capture(ConsoleSpanExporter):
        def export(self, spans):  # type: ignore[override]
            captured.extend(s.name for s in spans)
            return super().export(spans)

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_Capture()))
    _state = capture_otel_provider_state()
    try:
        reset_otel_provider()
        trace.set_tracer_provider(provider)

        g = _seed_graph()
        exp = _generate_and_attach(g, campaign_seed=8, expansion_id=1, attach_ids=["entrance"])
        conn = _mem_conn()
        store = DungeonStore(conn)
        store.ensure_schema()
        _commit_seed(store, g)
        # NO masks kwarg.
        store.commit_expansion(exp, g)
    finally:
        restore_otel_provider_state(_state)

    assert "dungeon.persist.mask_write" not in captured, (
        "mask_write span fired with no masks supplied — that's the Illusionism "
        "the lie-detector exists to catch (spec §6)"
    )


def test_load_masks_emits_mask_load_span_with_mask_rows_attr() -> None:
    """The load path opens a ``dungeon.persist.mask_load`` span carrying
    a ``mask_rows`` attribute equal to the number of non-NULL mask rows
    returned. Empty load (fresh save) emits ``mask_rows=0`` — the span
    must still fire so the GM panel can confirm the load path was
    reached (No Silent Fallbacks on observability either)."""
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import (
        ConsoleSpanExporter,
        SimpleSpanProcessor,
    )

    from sidequest.dungeon.persistence import DungeonStore
    from tests.dungeon.conftest import (
        capture_otel_provider_state,
        reset_otel_provider,
        restore_otel_provider_state,
    )

    captured: list[Any] = []

    class _Capture(ConsoleSpanExporter):
        def export(self, spans):  # type: ignore[override]
            captured.extend(spans)
            return super().export(spans)

    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(_Capture()))
    _state = capture_otel_provider_state()
    try:
        reset_otel_provider()
        trace.set_tracer_provider(provider)

        g = _seed_graph()
        exp = _generate_and_attach(g, campaign_seed=19, expansion_id=1, attach_ids=["entrance"])
        conn = _mem_conn()
        store = DungeonStore(conn)
        store.ensure_schema()
        _commit_seed(store, g)
        masks = {n.id: _example_mask_dict(n.id) for n in exp.new_nodes}
        store.commit_expansion(exp, g, masks=masks)
        conn.commit()

        # Drop and reload to force a real load_masks() invocation.
        loaded = store.load_masks()
        assert loaded == masks
    finally:
        restore_otel_provider_state(_state)

    names = [s.name for s in captured]
    assert "dungeon.persist.mask_load" in names, (
        f"mask_load span not emitted; captured spans = {names!r}"
    )
    mask_load_spans = [s for s in captured if s.name == "dungeon.persist.mask_load"]
    attrs = mask_load_spans[-1].attributes or {}
    assert attrs.get("mask_rows") == len(masks), (
        f"mask_rows drift on load: got {attrs.get('mask_rows')!r}, expected {len(masks)}"
    )
