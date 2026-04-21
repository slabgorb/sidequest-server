# Tension fixtures — Rust-canonical parity data

These JSON fixtures are produced by the Rust example in
`sidequest-api/crates/sidequest-game/examples/tension_fixture_export.rs`.
They are the parity contract for the Python port of `TensionTracker`,
`PacingHint`, and the two free classifier functions.

## Files

| File | Source | Used by |
|------|--------|---------|
| `classify_round.json` | `classify_round(round, killed)` | `test_classify_round_fixture_parity` |
| `classify_combat_outcome.json` | `classify_combat_outcome(round, killed, lowest_hp_ratio)` | `test_classify_combat_outcome_fixture_parity` |
| `scenario_escalating.json` | `TensionTracker` observe + tick + update_stakes sequence — escalating combat into a kill | `test_scenario_escalating_parity` |
| `scenario_stalling.json` | Sequence of boring observes that crosses `escalation_streak` threshold | `test_scenario_stalling_parity` |
| `scenario_reversal.json` | High-stakes spike into a long quiet decay | `test_scenario_reversal_parity` |

## Regeneration

When the Rust source changes — new variant in `DetailedCombatEvent`, new
field on `PacingHint`, classifier rule change — regenerate fixtures from
the Rust side, then re-run the Python tests:

```bash
cd sidequest-api
cargo run --example tension_fixture_export -p sidequest-game
# Optional: write somewhere else for diff inspection
cargo run --example tension_fixture_export -p sidequest-game -- /tmp/tension-fixtures
```

The example writes to `sidequest-server/tests/fixtures/tension/` by
default (resolved relative to the example's `CARGO_MANIFEST_DIR`).

## Schema

### `classify_round.json` / `classify_combat_outcome.json`

```jsonc
{
  "cases": [
    {
      "name": "kill_is_dramatic_even_with_no_damage",
      "input": {
        "round": 1,
        "damage_events": [],
        "effects_applied": [],
        "effects_expired": [],
        "killed": "orc"
        // For classify_combat_outcome the input is wrapped:
        // "input": { "round": {...}, "lowest_hp_ratio": 0.05 }
      },
      "expected": "Dramatic"
      // For classify_combat_outcome:
      // "expected": { "kind": "Dramatic", "event": "KillingBlow" }
    }
  ]
}
```

### Scenario files

Each scenario file lists steps that are replayed against a fresh
`TensionTracker`. Every step's `after` block captures the full tracker
state plus the `pacing_hint` produced *immediately after* the step:

```jsonc
{
  "name": "escalating",
  "thresholds": { "sentence_delivery_min": 0.30, "streaming_delivery_min": 0.70, ... },
  "steps": [
    {
      "step": { "kind": "observe", "round": {...}, "lowest_hp_ratio": 0.95 },
      "after": {
        "action_tension": 0.0,
        "stakes_tension": 0.0,
        "drama_weight": 0.0,
        "active_spike": 0.0,
        "boring_streak": 0,
        "classification": { "kind": "Normal" },
        "pacing_hint": {
          "drama_weight": 0.0,
          "target_sentences": 1,
          "delivery_mode": "Instant",
          "escalation_beat": null,
          "narrator_directive": "Target approximately 1 sentence(s) for this narration. Drama level: 0%."
        }
      }
    }
  ]
}
```

Step kinds: `observe`, `tick`, `update_stakes`. `tick` and
`update_stakes` steps have `classification: null`.

## Edge cases encoded

- `killed = ""` (empty string) is a kill — distinct from `killed = null`
  (no kill). `classify_round` and `classify_combat_outcome` both treat
  empty-string-killed as Dramatic / KillingBlow respectively.
- Negative damage is clamped to zero per damage event.
- `lowest_hp_ratio = null` skips the NearMiss check entirely.
- Multi-round scenarios cover the `escalation_streak` threshold (boring
  ramp), spike decay over quiet ticks, and stakes-driven drama on the
  reversal path.

## Why fixtures, not hand-written tables?

`DetailedCombatEvent` has 6 variants × 5 tracker states × multiple input
shapes. Hand-transcribing the classification tables into Python invites
copy-paste drift the moment Rust changes a threshold constant. The
exporter calls the *real Rust functions* and writes their output, so the
contract is self-updating at the touch of one cargo command.
