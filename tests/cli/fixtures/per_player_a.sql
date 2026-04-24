-- per_player_a.sql — Group D per-player diff fixture (player A)
--
-- Shares genre+world with per_player_b (caverns_and_claudes / test_vault).
-- Round 1 narrator content is IDENTICAL to player B's (diff should match).
-- Round 2 narrator content DIFFERS from player B's (diff should flag).
-- Round 3 is incidental (both sides have content, not compared for divergence).
-- Schema mirrors sidequest/game/persistence.py SCHEMA_SQL exactly.

CREATE TABLE IF NOT EXISTS session_meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    genre_slug TEXT NOT NULL,
    world_slug TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_played TEXT NOT NULL,
    schema_version INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS game_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    snapshot_json TEXT NOT NULL,
    saved_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS narrative_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_number INTEGER NOT NULL,
    author TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_narrative_round ON narrative_log(round_number);
CREATE INDEX IF NOT EXISTS idx_narrative_author ON narrative_log(author);
CREATE TABLE IF NOT EXISTS lore_fragments (
    id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    source TEXT NOT NULL,
    turn_created INTEGER,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_lore_category ON lore_fragments(category);
CREATE TABLE IF NOT EXISTS scenario_archive (
    session_id TEXT PRIMARY KEY,
    scenario_json TEXT NOT NULL,
    saved_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS scrapbook_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id INTEGER NOT NULL,
    scene_title TEXT,
    scene_type TEXT,
    location TEXT NOT NULL,
    image_url TEXT,
    narrative_excerpt TEXT NOT NULL,
    world_facts TEXT NOT NULL DEFAULT '[]',
    npcs_present TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_scrapbook_turn ON scrapbook_entries(turn_id);
CREATE TABLE IF NOT EXISTS games (
    slug TEXT PRIMARY KEY,
    mode TEXT NOT NULL CHECK (mode IN ('solo', 'multiplayer')),
    genre_slug TEXT NOT NULL,
    world_slug TEXT NOT NULL,
    claude_session_id TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_seq ON events (seq);
CREATE TABLE IF NOT EXISTS projection_cache (
    event_seq    INTEGER NOT NULL,
    player_id    TEXT NOT NULL,
    include      INTEGER NOT NULL,
    payload_json TEXT,
    PRIMARY KEY (event_seq, player_id),
    FOREIGN KEY (event_seq) REFERENCES events(seq)
);
CREATE INDEX IF NOT EXISTS idx_projection_cache_player ON projection_cache (player_id, event_seq);

-- -------------------------------------------------------------------------
-- Fixture rows — player A view of test_vault
-- -------------------------------------------------------------------------

INSERT INTO session_meta (id, genre_slug, world_slug, created_at, last_played, schema_version)
VALUES (1, 'caverns_and_claudes', 'test_vault', '2026-04-24T00:00:00Z', '2026-04-24T00:00:00Z', 1);

INSERT INTO games (slug, mode, genre_slug, world_slug, claude_session_id, created_at)
VALUES ('per-player-a-fixture', 'multiplayer', 'caverns_and_claudes', 'test_vault', NULL, '2026-04-24T00:00:00Z');

INSERT INTO events (seq, kind, payload_json, created_at) VALUES
  (1, 'SESSION_STARTED', '{"genre":"caverns_and_claudes","world":"test_vault","player":"A"}', '2026-04-24T00:00:01Z'),
  (2, 'NARRATION',       '{"round":1}', '2026-04-24T00:00:02Z'),
  (3, 'NARRATION',       '{"round":2}', '2026-04-24T00:00:03Z');

-- Round 1: SAME as player B (should NOT flag as diverging).
-- Round 2: DIFFERS from player B — A sees the locked door.
-- Round 3: irrelevant, included so miner still has mineable pairs.
INSERT INTO narrative_log (id, round_number, author, content, tags, created_at) VALUES
  (1, 1, 'narrator', 'The vault door groans open. Dust curls across cold flagstones.', NULL, '2026-04-24T00:00:01Z'),
  (2, 1, 'Avery',    'I step inside, weapon drawn.', NULL, '2026-04-24T00:00:01Z'),
  (3, 2, 'narrator', 'You spot a locked door in the far wall, iron bands stained with rust.', NULL, '2026-04-24T00:00:02Z'),
  (4, 2, 'Avery',    'I inspect the lock for traps.', NULL, '2026-04-24T00:00:02Z'),
  (5, 3, 'narrator', 'The lock is simple — a tumbler, not a rune-ward. Your picks will do.', NULL, '2026-04-24T00:00:03Z'),
  (6, 3, 'Avery',    'I pick it.', NULL, '2026-04-24T00:00:03Z');
