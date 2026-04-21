# Encounter Fixtures — Rust-parity JSON

Canonical JSON blobs that Rust `serde_json::to_string(&StructuredEncounter)`
would produce. Hand-authored for 42-1 because `sidequest-game` has no
`examples/` directory yet — see Design Deviation in
`.session/42-1-session.md` (TEA).

Python StructuredEncounter must:
1. Accept each fixture via `model_validate_json(...)` without error.
2. Re-serialize to an equivalent JSON blob (field-order tolerant; use
   pydantic's canonical `model_dump_json`).

If the Python side evolves to require different field ordering or key
naming, regenerate these fixtures via a `cargo run --example encounter_fixture`
in `sidequest-api/crates/sidequest-game/examples/` and commit the new
output here. Do NOT hand-edit — the point is byte parity with the Rust
serializer.

## Contents

| File | Source |
|------|--------|
| `combat_alice_bob_hp30.json` | `StructuredEncounter::combat(vec!["Alice".into(), "Bob".into()], 30)` |
| `chase_interceptor_goal10.json` | `StructuredEncounter::chase(0.5, Some(RigType::Interceptor), 10)` |
| `chase_no_rig_goal10.json` | `StructuredEncounter::chase(0.5, None, 10)` — minimal chase |
| `standoff_full.json` | Full standoff with actors, secondary stats, narrator_hints |
