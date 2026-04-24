"""REST API endpoints for sidequest-server Phase 1.

Endpoints:
  GET /api/genres    — list available genre packs with world metadata (lobby picker)
  GET /api/saves     — list saves for a genre/world/player
  POST /api/saves/new — create a new save slot (init empty session)
  DELETE /api/saves/{genre}/{world}/{player} — delete a save file
  GET /api/sessions  — list active sessions (Phase 1: always empty; multiplayer is Phase N)

Port of lib.rs list_genres() + persistence REST surface.
Wire format matches the Rust server's response shapes exactly — the existing
React UI reads these endpoints.
"""

from __future__ import annotations

import logging
from datetime import date as _date_cls
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from sidequest.game.game_slug import generate_slug
from sidequest.game.persistence import (
    GameMode,
    SqliteStore,
    db_path_for_session,
    db_path_for_slug,
    get_game,
    upsert_game,
)
from sidequest.game.session import GameSnapshot
from sidequest.genre.loader import DEFAULT_GENRE_PACK_SEARCH_PATHS

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class WorldMeta(BaseModel):
    """Lobby metadata for a single world. Mirrors Rust WorldResponse."""

    slug: str
    name: str
    description: str
    era: str | None = None
    setting: str | None = None
    inspirations: list[str] = []
    axis_snapshot: dict[str, float] = {}
    hero_image: str | None = None


class GenreMeta(BaseModel):
    """Lobby metadata for a genre. Mirrors Rust GenreResponse."""

    name: str
    description: str
    worlds: list[WorldMeta] = []


class SaveEntry(BaseModel):
    """A single save file entry."""

    genre_slug: str
    world_slug: str
    player_name: str
    db_path: str
    turn: int = 0
    location: str = ""
    last_saved: str = ""


class SessionPlayer(BaseModel):
    """A player in an active session."""

    player_id: str
    display_name: str


class ActiveSession(BaseModel):
    """An active session (Phase 1: always empty — multiplayer deferred)."""

    session_key: str
    genre: str
    world: str
    session_id: str
    players: list[SessionPlayer] = []
    current_turn: int = 0
    current_location: str = ""
    turn_mode: str = "free_play"


class CreateGameRequest(BaseModel):
    genre_slug: str
    world_slug: str
    mode: GameMode  # pydantic rejects unknown enum values with 422


class GameResponse(BaseModel):
    slug: str
    mode: GameMode
    genre_slug: str
    world_slug: str
    resumed: bool


# ---------------------------------------------------------------------------
# Router factory (takes search_paths + save_dir from app.state)
# ---------------------------------------------------------------------------


def create_rest_router() -> APIRouter:
    """Create the REST API router.

    Handlers pull config from request.app.state:
      - app.state.genre_pack_search_paths: list[Path]
      - app.state.save_dir: Path
    """
    router = APIRouter()

    @router.get("/api/genres")
    async def list_genres(request: Request) -> dict[str, Any]:
        """List available genre packs with lobby-ready world metadata.

        Mirrors Rust list_genres() in sidequest-server/src/lib.rs.
        Returns { genre_slug: { name, description, worlds: [...] } }.
        Malformed packs are logged and skipped — one broken pack must not
        break the entire lobby.
        """
        search_paths: list[Path] = getattr(
            request.app.state,
            "genre_pack_search_paths",
            DEFAULT_GENRE_PACK_SEARCH_PATHS,
        )

        # Find first valid genre_packs directory
        packs_path: Path | None = None
        for sp in search_paths:
            if sp.exists() and sp.is_dir():
                packs_path = sp
                break

        if packs_path is None:
            logger.warning(
                "list_genres: no genre packs directory found in %s",
                [str(p) for p in search_paths],
            )
            return {}

        genres: dict[str, Any] = {}

        for entry in sorted(packs_path.iterdir()):
            if not entry.is_dir():
                continue
            genre_slug = entry.name

            pack_yaml_path = entry / "pack.yaml"
            if not pack_yaml_path.exists():
                continue

            # Parse pack.yaml for name + description
            try:
                raw = yaml.safe_load(pack_yaml_path.read_text(encoding="utf-8"))
                name = str(raw.get("name", genre_slug))
                description = str(raw.get("description", ""))
            except Exception as exc:
                logger.warning(
                    "list_genres: skipping '%s' — pack.yaml failed: %s",
                    genre_slug,
                    exc,
                )
                continue

            # Walk worlds/ subdirectory
            worlds_dir = entry / "worlds"
            worlds: list[dict[str, Any]] = []
            if worlds_dir.exists():
                for world_entry in sorted(worlds_dir.iterdir()):
                    if not world_entry.is_dir():
                        continue
                    world_slug = world_entry.name
                    world_yaml_path = world_entry / "world.yaml"
                    if not world_yaml_path.exists():
                        logger.warning(
                            "list_genres: skipping world '%s/%s' — world.yaml missing",
                            genre_slug,
                            world_slug,
                        )
                        continue

                    try:
                        wraw = yaml.safe_load(world_yaml_path.read_text(encoding="utf-8"))
                    except Exception as exc:
                        logger.warning(
                            "list_genres: skipping world '%s/%s' — world.yaml parse: %s",
                            genre_slug,
                            world_slug,
                            exc,
                        )
                        continue

                    wname = str(wraw.get("name", world_slug))
                    wdesc = str(wraw.get("description", ""))
                    wera = wraw.get("era")
                    wsetting = wraw.get("setting")
                    winsp = wraw.get("inspirations", [])
                    if not isinstance(winsp, list):
                        winsp = []
                    winsp = [str(i) for i in winsp]
                    waxis = wraw.get("axis_snapshot", {})
                    if not isinstance(waxis, dict):
                        waxis = {}

                    # Resolve cover_poi → hero_image URL
                    cover_poi = wraw.get("cover_poi")
                    hero_image: str | None = None
                    if cover_poi:
                        poi_dir = world_entry / "assets" / "poi"
                        for ext in ("jpg", "png", "webp"):
                            candidate = poi_dir / f"{cover_poi}.{ext}"
                            if candidate.exists():
                                hero_image = (
                                    f"/genre/{genre_slug}/worlds/{world_slug}"
                                    f"/assets/poi/{cover_poi}.{ext}"
                                )
                                break
                        if hero_image is None:
                            logger.warning(
                                "list_genres: cover_poi '%s' not found for %s/%s",
                                cover_poi,
                                genre_slug,
                                world_slug,
                            )
                    else:
                        logger.warning(
                            "list_genres: no cover_poi in world.yaml for %s/%s — "
                            "lobby preview will show placeholder",
                            genre_slug,
                            world_slug,
                        )

                    worlds.append(
                        {
                            "slug": world_slug,
                            "name": wname,
                            "description": wdesc,
                            "era": wera,
                            "setting": wsetting,
                            "inspirations": winsp,
                            "axis_snapshot": waxis,
                            "hero_image": hero_image,
                        }
                    )

            genres[genre_slug] = {
                "name": name,
                "description": description,
                "worlds": worlds,
            }

        return genres

    @router.get("/api/saves", deprecated=True)
    async def list_saves(request: Request) -> dict[str, Any]:
        """List save files matching optional genre/world/player filters.

        Query params: ?genre=..., ?world=..., ?player=...
        Returns { saves: [SaveEntry, ...] }.
        """
        logger.warning("legacy GET /api/saves called — prefer POST /api/games")
        save_dir: Path = getattr(
            request.app.state,
            "save_dir",
            Path.home() / ".sidequest" / "saves",
        )
        genre_filter = request.query_params.get("genre")
        world_filter = request.query_params.get("world")
        player_filter = request.query_params.get("player")

        if not save_dir.exists():
            return {"saves": []}

        saves: list[dict[str, Any]] = []

        # Walk save_dir/{genre}/{world}/{player}/save.db
        for genre_dir in sorted(save_dir.iterdir()):
            if not genre_dir.is_dir():
                continue
            genre_slug = genre_dir.name
            if genre_filter and genre_slug != genre_filter:
                continue

            for world_dir in sorted(genre_dir.iterdir()):
                if not world_dir.is_dir():
                    continue
                world_slug = world_dir.name
                if world_filter and world_slug != world_filter:
                    continue

                for player_dir in sorted(world_dir.iterdir()):
                    if not player_dir.is_dir():
                        continue
                    player_name = player_dir.name
                    if player_filter and player_name != player_filter:
                        continue

                    db_file = player_dir / "save.db"
                    if not db_file.exists():
                        continue

                    # Read minimal metadata from the save
                    turn = 0
                    location = ""
                    last_saved = ""
                    try:
                        store = SqliteStore.open(str(db_file))
                        saved = store.load()
                        store.close()
                        if saved is not None:
                            turn = saved.snapshot.turn_manager.interaction
                            location = saved.snapshot.location or ""
                            if saved.meta.last_played:
                                last_saved = saved.meta.last_played.isoformat()
                    except Exception as exc:
                        logger.warning(
                            "list_saves: could not read %s: %s",
                            db_file,
                            exc,
                        )

                    saves.append(
                        {
                            "genre_slug": genre_slug,
                            "world_slug": world_slug,
                            "player_name": player_name,
                            "db_path": str(db_file),
                            "turn": turn,
                            "location": location,
                            "last_saved": last_saved,
                        }
                    )

        return {"saves": saves}

    @router.post("/api/saves/new", deprecated=True)
    async def create_save(request: Request) -> dict[str, Any]:
        """Create a new save slot (initialize empty session).

        Body JSON: { genre_slug, world_slug, player_name }
        Returns { db_path: "..." }.
        """
        logger.warning("legacy POST /api/saves/new called — prefer POST /api/games")
        save_dir: Path = getattr(
            request.app.state,
            "save_dir",
            Path.home() / ".sidequest" / "saves",
        )
        try:
            body = await request.json()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc

        genre_slug = body.get("genre_slug", "")
        world_slug = body.get("world_slug", "")
        player_name = body.get("player_name", "player")

        if not genre_slug:
            raise HTTPException(status_code=400, detail="genre_slug is required")
        if not world_slug:
            raise HTTPException(status_code=400, detail="world_slug is required")

        db_path = db_path_for_session(save_dir, genre_slug, world_slug, player_name)
        db_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            store = SqliteStore.open(str(db_path))
            store.init_session(genre_slug, world_slug)
            initial_snapshot = GameSnapshot(
                genre_slug=genre_slug,
                world_slug=world_slug,
                location="",
            )
            store.save(initial_snapshot)
            store.close()
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Failed to create save: {exc}"
            ) from exc

        logger.info(
            "rest.save_created genre=%s world=%s player=%s",
            genre_slug,
            world_slug,
            player_name,
        )
        return {"db_path": str(db_path), "genre_slug": genre_slug, "world_slug": world_slug, "player_name": player_name}

    @router.delete("/api/saves/{genre_slug}/{world_slug}/{player_name}", deprecated=True)
    async def delete_save(
        genre_slug: str,
        world_slug: str,
        player_name: str,
        request: Request,
    ) -> dict[str, Any]:
        """Delete a save file.

        Raises 404 if the save does not exist.
        """
        logger.warning("legacy DELETE /api/saves/{%s}/{%s}/{%s} called — prefer POST /api/games", genre_slug, world_slug, player_name)
        save_dir: Path = getattr(
            request.app.state,
            "save_dir",
            Path.home() / ".sidequest" / "saves",
        )
        db_path = db_path_for_session(save_dir, genre_slug, world_slug, player_name)

        if not db_path.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Save not found: {genre_slug}/{world_slug}/{player_name}",
            )

        try:
            db_path.unlink()
            # Remove empty parent directories (player/ world/ genre/)
            for parent in (db_path.parent, db_path.parent.parent, db_path.parent.parent.parent):
                try:
                    parent.rmdir()  # only removes if empty
                except OSError:
                    break
        except Exception as exc:
            raise HTTPException(
                status_code=500, detail=f"Failed to delete save: {exc}"
            ) from exc

        logger.info(
            "rest.save_deleted genre=%s world=%s player=%s",
            genre_slug,
            world_slug,
            player_name,
        )
        return {"deleted": True, "genre_slug": genre_slug, "world_slug": world_slug, "player_name": player_name}

    @router.get("/api/sessions")
    async def list_sessions(request: Request) -> dict[str, Any]:
        """List active sessions.

        Phase 1: single-player only — always returns empty sessions list.
        Phase N: multiplayer SharedGameSession sync.
        """
        return {"sessions": []}

    @router.get("/api/debug/state")
    async def debug_state(request: Request) -> list[dict[str, Any]]:
        """Enumerate persisted game sessions for the GM dashboard State tab.

        Walks ``<save_dir>/games/<slug>/save.db`` and projects each loaded
        :class:`GameSnapshot` onto the ``SessionStateView`` shape defined in
        ``sidequest-ui/src/types/watcher.ts``. Read-only; broken / empty DB
        files are skipped rather than failing the request.
        """
        save_dir: Path = request.app.state.save_dir
        games_root = save_dir / "games"
        views: list[dict[str, Any]] = []
        if not games_root.exists():
            return views
        for slug_dir in sorted(games_root.iterdir()):
            db_file = slug_dir / "save.db"
            if not db_file.is_file():
                continue
            try:
                store = SqliteStore.open(str(db_file))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "debug_state.store_open_failed slug=%s error=%s",
                    slug_dir.name,
                    exc,
                )
                continue
            try:
                saved = store.load()
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "debug_state.snapshot_load_failed slug=%s error=%s",
                    slug_dir.name,
                    exc,
                )
                store.close()
                continue
            store.close()
            if saved is None:
                continue
            snap = saved.snapshot
            npc_registry: list[dict[str, Any]] = []
            for entry in snap.npc_registry:
                npc_registry.append(
                    {
                        "name": entry.name or "",
                        "pronouns": entry.pronouns or "",
                        "role": entry.role or "",
                        "location": entry.last_seen_location or "",
                        "last_seen_turn": entry.last_seen_turn or 0,
                        "age": "",
                        "appearance": entry.appearance or "",
                        "ocean_summary": None,
                        "ocean": None,
                        "hp": 0,
                        "max_hp": 0,
                    }
                )
            trope_states: list[dict[str, Any]] = []
            for trope in snap.active_tropes:
                trope_states.append(
                    {
                        "trope_definition_id": getattr(trope, "trope_id", ""),
                        "status": str(getattr(trope, "status", "")),
                        "progression": int(getattr(trope, "progression", 0) or 0),
                    }
                )
            players: list[dict[str, Any]] = []
            for char in snap.characters:
                hp = getattr(char, "hp", None)
                max_hp = getattr(char, "hp_max", None)
                # Character.name / Character.level are Combatant-equivalent
                # methods (Rust port — see sidequest/game/character.py:148-162),
                # not attributes. getattr returns the bound method; call it.
                name_attr = getattr(char, "name", None)
                level_attr = getattr(char, "level", 1)
                resolved_name = name_attr() if callable(name_attr) else name_attr
                resolved_level = level_attr() if callable(level_attr) else level_attr
                players.append(
                    {
                        "player_name": getattr(char, "player_name", "") or "",
                        "character_name": resolved_name,
                        "character_class": getattr(char, "archetype", "") or "",
                        "character_hp": int(hp) if hp is not None else 0,
                        "character_max_hp": int(max_hp) if max_hp is not None else 0,
                        "character_level": int(resolved_level or 1),
                        "character_xp": int(getattr(char, "xp", 0) or 0),
                        "region_id": snap.current_region or "",
                        "display_location": snap.location or "",
                        "inventory": {
                            "items": [],
                            "gold": 0,
                        },
                    }
                )
            views.append(
                {
                    "session_key": slug_dir.name,
                    "genre_slug": snap.genre_slug or "",
                    "world_slug": snap.world_slug or "",
                    "current_location": snap.location or "",
                    "discovered_regions": list(snap.discovered_regions),
                    "narration_history_len": len(snap.narrative_log),
                    "turn_mode": str(snap.turn_manager.phase),
                    "npc_registry": npc_registry,
                    "trope_states": trope_states,
                    "players": players,
                    "player_count": len(players),
                    "has_music_director": False,
                    "has_audio_mixer": False,
                    "region_names": [],
                }
            )
        return views

    @router.post("/api/games", status_code=201)
    async def create_or_resume_game(req: CreateGameRequest, request: Request) -> Any:
        """Create a new game (201) or resume an existing same-slug game (200, resumed=True).

        The slug is derived from world_slug + today's date. If a game already
        exists for that slug, it is returned in frozen mode — the original mode,
        genre_slug, and world_slug are preserved and the new request's mode is
        ignored.
        """
        from sidequest.telemetry.spans import mp_game_created_span

        save_dir: Path = request.app.state.save_dir
        today_fn = getattr(request.app.state, "today_fn", _date_cls.today)
        slug = generate_slug(world_slug=req.world_slug, today=today_fn())
        db = db_path_for_slug(save_dir, slug)
        db.parent.mkdir(parents=True, exist_ok=True)
        store = SqliteStore(db)
        store.initialize()

        existing = get_game(store, slug)
        if existing is not None:
            # Existing row "wins" — emit span with the frozen metadata so GM
            # panel sees which mode/genre/world are actually in effect, not
            # what the client requested.
            with mp_game_created_span(
                slug=slug,
                mode=str(existing.mode.value) if hasattr(existing.mode, "value") else str(existing.mode),
                genre_slug=existing.genre_slug,
                world_slug=existing.world_slug,
                resumed=True,
            ):
                payload = GameResponse(
                    slug=slug, mode=existing.mode,
                    genre_slug=existing.genre_slug, world_slug=existing.world_slug,
                    resumed=True,
                )
                return JSONResponse(status_code=200, content=payload.model_dump())

        with mp_game_created_span(
            slug=slug,
            mode=str(req.mode.value) if hasattr(req.mode, "value") else str(req.mode),
            genre_slug=req.genre_slug,
            world_slug=req.world_slug,
            resumed=False,
        ):
            upsert_game(store, slug=slug, mode=req.mode,
                        genre_slug=req.genre_slug, world_slug=req.world_slug)
            return GameResponse(
                slug=slug, mode=req.mode,
                genre_slug=req.genre_slug, world_slug=req.world_slug,
                resumed=False,
            )

    @router.get("/api/games/{slug}")
    async def get_game_endpoint(slug: str, request: Request) -> GameResponse:
        """Return metadata for a game by slug.

        Raises 404 if no game with that slug exists.
        """
        save_dir: Path = request.app.state.save_dir
        db = db_path_for_slug(save_dir, slug)
        if not db.exists():
            raise HTTPException(status_code=404, detail=f"no game with slug {slug}")
        store = SqliteStore(db)
        store.initialize()
        row = get_game(store, slug)
        if row is None:
            raise HTTPException(status_code=404, detail=f"no game with slug {slug}")
        return GameResponse(
            slug=row.slug, mode=row.mode,
            genre_slug=row.genre_slug, world_slug=row.world_slug,
            resumed=True,
        )

    return router
