-- single_session.sql — Group D corpus-mining fixture
--
-- One genre/world (caverns_and_claudes / mawdeep), 3 events, 3 narrative_log rows.
-- Round 1: narrator-only opening (miner will skip — no player pair).
-- Round 2: narrator + player pair.
-- Round 3: narrator + player pair.
-- Schema is the authoritative one from sidequest/game/persistence.py SCHEMA_SQL.
-- All timestamps are deterministic so regenerated .db files are byte-reproducible
-- (modulo SQLite's header, which is stable for identical input).

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
-- Fixture rows
-- -------------------------------------------------------------------------

INSERT INTO session_meta (id, genre_slug, world_slug, created_at, last_played, schema_version)
VALUES (1, 'caverns_and_claudes', 'mawdeep', '2026-04-24T00:00:00Z', '2026-04-24T00:00:00Z', 1);

INSERT INTO games (slug, mode, genre_slug, world_slug, claude_session_id, created_at)
VALUES ('single-session-fixture', 'solo', 'caverns_and_claudes', 'mawdeep', NULL, '2026-04-24T00:00:00Z');

-- Three events so mine_save sees a non-empty events table.
INSERT INTO events (seq, kind, payload_json, created_at) VALUES
  (1, 'SESSION_STARTED', '{"genre":"caverns_and_claudes","world":"mawdeep"}', '2026-04-24T00:00:01Z'),
  (2, 'NARRATION',       '{"round":2,"author":"narrator"}',                   '2026-04-24T00:00:02Z'),
  (3, 'NARRATION',       '{"round":3,"author":"narrator"}',                   '2026-04-24T00:00:03Z');

-- Narrative log: round 1 narrator-only (skipped by miner),
-- rounds 2 and 3 narrator + player (mineable pairs).
INSERT INTO narrative_log (id, round_number, author, content, tags, created_at) VALUES
  (1, 1, 'narrator', 'You stand at the lip of the Mawdeep, torchlight flickering against wet stone.', NULL, '2026-04-24T00:00:01Z'),
  (2, 2, 'narrator', 'A low tunnel branches left. Cold air sighs from it, smelling of iron and mould.', NULL, '2026-04-24T00:00:02Z'),
  (3, 2, 'Rux',      'I ready my lantern and creep into the left tunnel, listening hard.',              NULL, '2026-04-24T00:00:02Z'),
  (4, 3, 'narrator', 'The tunnel opens into a chamber. Something clinks against your boot — a bone key.', NULL, '2026-04-24T00:00:03Z'),
  (5, 3, 'Rux',      'I pocket the key and hold the lantern high to sweep the chamber.',                NULL, '2026-04-24T00:00:03Z');
