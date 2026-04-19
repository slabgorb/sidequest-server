# sidequest-server

Python FastAPI server for SideQuest — port target of `sidequest-api` (Rust).

## Status

**Pre-port.** This repo is a placeholder. The Python implementation is being ported from `sidequest-api` per [ADR-082](../docs/adr/082-port-api-rust-to-python.md).

## Why this repo exists

The Rust port of the SideQuest backend (`sidequest-api`) hit a structural conflict with the developer workflow — cargo's concurrency model (global advisory locks, env-fingerprinted incremental caches, sccache daemon contention) is hostile to a parallel two-clone, multi-agent development environment. After ~20 days and ~1,800 commits, the cost/benefit inverted.

This repo is the Python destination. See ADR-082 for the full rationale, port strategy, and compatibility guarantees.

## Composition

Follows a **1:1 mapping** of the Rust crate structure. Each crate in `sidequest-api/crates/` maps to a package of the same name under `sidequest/`:

| Rust crate | Python package |
|------------|----------------|
| `sidequest-protocol` | `sidequest.protocol` |
| `sidequest-genre` | `sidequest.genre` |
| `sidequest-game` | `sidequest.game` |
| `sidequest-agents` | `sidequest.agents` |
| `sidequest-daemon-client` | `sidequest.daemon_client` |
| `sidequest-server` | `sidequest.server` |
| `sidequest-telemetry` | `sidequest.telemetry` |
| `sidequest-promptpreview` | `sidequest.cli.promptpreview` |
| `sidequest-encountergen` | `sidequest.cli.encountergen` |
| `sidequest-loadoutgen` | `sidequest.cli.loadoutgen` |
| `sidequest-namegen` | `sidequest.cli.namegen` |
| `sidequest-validate` | `sidequest.cli.validate` |

The 1:1 discipline is load-bearing: it allows feature-by-feature comparison between the Rust reference tree and the Python port by path rather than archaeology.

## Branching

Gitflow. `develop` is the default integration branch. `main` tracks releases.

## Scope

- **In scope:** Full port of `sidequest-api` to Python.
- **Out of scope:** `sidequest-ui` (unchanged), `sidequest-content` (unchanged), `sidequest-daemon` (unchanged — serves uses beyond SideQuest).
