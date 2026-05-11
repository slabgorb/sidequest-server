# Changelog

All notable changes to the SideQuest game-engine backend.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.1.0] - 2026-05-11

### Added
- **World items.yaml loader** — surfaces `named_items`, `modifier_items`,
  `reliquaries`, `crimson_remnants`, and `consumable_items` to `World.items`
  as a `WorldItemsCatalog`. Loud-fails on duplicate ids across sections,
  malformed YAML, or missing id/name. Emits `state_transition:world_items:loaded`
  OTEL watcher event with per-section counts.
- **Cleric divine_favor reliquary invocation** — `invoke_reliquary` op with
  four typed-reason gates (no_divine_favor_bar, favor_below_threshold,
  unknown_reliquary, reliquary_missing_effect, free_use_already_spent),
  default threshold 0.7, once-per-session token tracked on
  `MagicState.reliquary_free_use_spent`. Emits `magic.invoke_reliquary`
  watcher event on success.
- **Narrator reliquary context** — `build_magic_context_block` now renders
  an `<available-reliquaries>` section carrying each eligible reliquary's
  verbatim `divine_favor_effect` text when the Cleric passes all gates.
  `TurnContext.world_items` plumbed through `run_narration_turn`.
- **Stateless narrator (ADR-098)** — narrator turns no longer use
  `--resume`; each turn ships a bounded per-turn prompt. Stale degraded-
  result tests retired.
- **MP TURN_STATUS broadcast** — per-player turn status with claude CLI
  stderr capture (47-5).
- **B/X B26 saving throws** — schema + resolver + OTEL coverage; per-class
  saving-throw tables required when the pack ships spell catalogs.
- **C&C B/X class beats** — beat filters by class; `prepare`/`cast`/`rest`/
  `turn_undead` ops on `learned_v1`; per-spell-level slot ledger bars.
- **Cold-subsystem OTEL coverage** — genre pack load, cache hit/miss,
  unrouted magic costs, and items catalog load all emit watcher events.
- **Cinematic narrator default** — verbosity/vocabulary defaults shipped
  for live play.

### Changed
- Audio config path resolution drops `Path.exists()` guard so R2-only
  packs resolve via URL.
- Chargen Edge seed += CON modifier (story 39-9 / 39-10).
- Confrontation difficulty calibration v1 (ADR-093).
- `MagicState.spent_spells` exposes UI strikethrough state for cast
  spells until rest.

### Fixed
- `_degraded_result` correctly sets `is_degraded=True`.
- ACE-Step output fields stop creeping back into music params JSON via
  output-only treatment in daemon.

## [1.0.0] - prior

Initial Python port from the Rust `sidequest-api` prototype per ADR-082.
Not formally tagged at the time; recorded here for continuity.
