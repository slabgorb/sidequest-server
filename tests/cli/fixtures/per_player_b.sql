-- per_player_b.sql — Group D per-player diff fixture (player B)
--
-- Shares genre+world with per_player_a (caverns_and_claudes / test_vault).
-- Round 1 narrator content is IDENTICAL to player A's.
-- Round 2 narrator content DIFFERS from player A's — B wanders an empty corridor
-- instead of spotting the locked door. This is the single diverging row the diff
-- test is expected to flag.
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
-- Fixture rows — player B view of test_vault
-- -------------------------------------------------------------------------

INSERT INTO session_meta (id, genre_slug, world_slug, created_at, last_played, schema_version)
VALUES (1, 'caverns_and_claudes', 'test_vault', '2026-04-24T00:00:00Z', '2026-04-24T00:00:00Z', 1);

INSERT INTO games (slug, mode, genre_slug, world_slug, claude_session_id, created_at)
VALUES ('per-player-b-fixture', 'multiplayer', 'caverns_and_claudes', 'test_vault', NULL, '2026-04-24T00:00:00Z');

INSERT INTO events (seq, kind, payload_json, created_at) VALUES
  (1, 'SESSION_STARTED', '{"genre":"caverns_and_claudes","world":"test_vault","player":"B"}', '2026-04-24T00:00:01Z'),
  (2, 'NARRATION',       '{"round":1}', '2026-04-24T00:00:02Z'),
  (3, 'NARRATION',       '{"round":2}', '2026-04-24T00:00:03Z');

-- Round 1: IDENTICAL narrator text to player A.
-- Round 2: DIVERGES from player A — empty corridor instead of locked door.
-- Round 3: irrelevant (different content from A, but diff test only asserts
-- round 2 is flagged).
INSERT INTO narrative_log (id, round_number, author, content, tags, created_at) VALUES
  (1, 1, 'narrator', 'The vault door groans open. Dust curls across cold flagstones.', NULL, '2026-04-24T00:00:01Z'),
  (2, 1, 'Brennan',  'I step inside, weapon drawn.', NULL, '2026-04-24T00:00:01Z'),
  (3, 2, 'narrator', 'You wander an empty corridor. Only your footsteps answer you.', NULL, '2026-04-24T00:00:02Z'),
  (4, 2, 'Brennan',  'I keep moving, senses peeled for anything out of place.', NULL, '2026-04-24T00:00:02Z'),
  (5, 3, 'narrator', 'The corridor bends north into a shallow alcove, empty save for a chipped sconce.', NULL, '2026-04-24T00:00:03Z'),
  (6, 3, 'Brennan',  'I peer into the alcove.', NULL, '2026-04-24T00:00:03Z');
