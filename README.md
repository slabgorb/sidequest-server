# sidequest-server

Python FastAPI game engine for SideQuest — the live backend (port 8765). Hosts the WebSocket transport, narrator orchestration, genre pack runtime, and game state.

See [ADR-082](../docs/adr/082-port-api-rust-to-python.md) for the port history; the Rust prototype `sidequest-api` is preserved read-only at <https://github.com/slabgorb/sidequest-api> for archaeology only.

## Quick start

```bash
uv sync                           # Install deps
uv run pytest -v                  # Tests
uv run ruff check .               # Lint
uv run uvicorn sidequest.server.app:app --reload --port 8765   # Boot
```

From the orchestrator root: `just server`, `just server-test`, `just server-check`.

## Stack

- **FastAPI** + **uvicorn** — HTTP, WebSocket, static file serving
- **pydantic v2** — Typed protocol (`GameMessage` discriminated union) and genre pack models
- **sqlite3** — Save persistence at `~/.sidequest/saves/`, one DB per genre/world session
- **PyYAML** — Genre pack loader (read-only at runtime)
- **OpenTelemetry** — Span emission for the GM dashboard (ADR-058, ADR-090)
- **websockets** — Watcher channel transport
- **uv** — Dependency management; `pyproject.toml` is the source of truth
- **Python 3.12+**

LLM calls go through the **Claude CLI** subprocess (`claude -p`, ADR-001) — no Anthropic SDK in the dependency graph. Media generation goes over a Unix socket to `sidequest-daemon` (ADR-035).

## Package layout

```
sidequest/
├── protocol/         # GameMessage, typed payloads (pydantic v2)
├── server/           # FastAPI app, WebSocket, dispatch, sessions, watcher
├── handlers/         # Per-message-type dispatch handlers
├── agents/           # claude -p subprocess orchestration (narrator + auxiliaries)
├── game/             # State, characters, encounters, tropes, turns, persistence (~70 modules)
├── genre/            # YAML loader, layered genre/world pack models
├── audio/            # Server-side music + SFX coordination
├── media/            # Image generation orchestration (daemon client wrapper)
├── magic/            # Magic system mechanics
├── interior/         # Room / interior state
├── orbital/          # Orbital / space-scene mechanics
├── corpus/           # Conlang corpus + Markov naming (ADR-091)
├── renderer/         # Render scheduling + throttle (ADR-050)
├── daemon_client/    # Unix-socket client for the media daemon
├── telemetry/        # OTEL span definitions and watcher hooks
└── cli/              # Standalone CLIs (see below)
```

The package composition mirrors the Rust crate layout 1:1 (per ADR-082) so historical features can be traced by path. Post-port refactoring is a separate decision.

## CLIs

Entry points under `sidequest/cli/`:

| CLI | Purpose |
|-----|---------|
| `encountergen` | Pre-generate encounter rosters into the Monster Manual (ADR-059) |
| `loadoutgen` | Generate loadout tables |
| `namegen` | Markov-generated names from culture corpora (ADR-091) |
| `validate` | Validate a genre pack against schema |
| `corpusmine` | Mine word lists from text |
| `corpuslabel` | Annotate corpus entries |
| `corpusdiff` | Diff two corpora |

Run via `uv run python -m sidequest.cli.<name>` or the installed console scripts.

## Endpoints

- **WebSocket `/ws`** — Primary game transport. `GameMessage` JSON in/out, discriminated on `type`. See [`docs/api-contract.md`](../docs/api-contract.md).
- **WebSocket `/ws/watcher`** — OTEL telemetry stream for the GM dashboard.
- **REST** — Small surface: `/api/genres`, save list, character list, scene harness (ADR-092, dev-gated).
- **Static** — `/renders/*` for daemon-produced images, `/dashboard` for the GM UI.

## Session model

- One WebSocket connection = one asyncio task owning a `Session`.
- Solo: one session, one orchestrator, no contention.
- Multiplayer: sessions share a `SessionRoom` keyed by `genre:world` behind `asyncio.Lock`, with `TurnBarrier` for coordinated turn resolution (ADR-036, ADR-037).
- Three turn modes: `FREE_PLAY`, `STRUCTURED` (submit-and-wait), `CINEMATIC`. Peer action text is visible during the structured wait phase (collaborative default per ADR-036 amendment).

## Key ADRs

- **ADR-067** Unified narrator agent — one persistent Opus session handles exploration, dialogue, combat, and chase narration. Auxiliary agents run off the critical path.
- **ADR-059** Monster Manual — NPCs and encounters are pre-generated server-side via CLI and injected into the narrator's `<game_state>` block. Narrator-side tool calling was abandoned.
- **ADR-038** WebSocket transport — reader/writer task split, broadcast channels.
- **ADR-005** Background-first — only text narration is on the critical path; media, deltas, trope tick, lore accumulation run async.

See [`docs/architecture.md`](../docs/architecture.md) for the full system design and [`docs/adr/`](../docs/adr/) for all decisions.

## Game state and saves

- **Save format:** SQLite `.db` files at `~/.sidequest/saves/<genre>_<world>.db` (not in repo).
- **Narrative log:** Append-only.
- **KnownFacts:** Accumulate across turns with provenance.
- DB calls run on a worker thread via `asyncio.to_thread`.

## Testing

```bash
uv run pytest -v                  # Full suite
uv run pytest tests/test_X.py     # One file
uv run pytest -k "name pattern"   # Filter
```

`pytest-asyncio` is configured for `asyncio_mode = "auto"`. Tests run against in-memory state — no daemon required for the default suite.

## Branching

Gitflow. `develop` is the integration branch. `main` tracks releases. PRs target `develop`.

## Related repos

- [orc-quest](https://github.com/slabgorb/orc-quest) — Orchestrator, ADRs, sprint tracking
- [sidequest-ui](https://github.com/slabgorb/sidequest-ui) — React client
- [sidequest-daemon](https://github.com/slabgorb/sidequest-daemon) — Python media services (Z-Image, ACE-Step)
- [sidequest-content](https://github.com/slabgorb/sidequest-content) — Genre packs (single source of truth)
