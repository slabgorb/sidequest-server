"""Standalone CLI for validating a genre pack's projection.yaml.

Usage:
    python -m sidequest.cli.validate.projection_check <genre_dir>

Exits 0 on success (prints audit table), nonzero on validation error
(prints error to stderr).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sidequest.game.projection.rules import (
    IncludeIfRule,
    RedactFieldsRule,
    TargetOnlyRule,
    load_rules_from_yaml_path,
)
from sidequest.game.projection.validator import (
    ValidationError,
    validate_projection_rules,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sidequest.cli.validate.projection_check",
        description="Validate a genre pack's projection.yaml and print the rule audit table.",
    )
    parser.add_argument(
        "genre_dir",
        type=Path,
        help="Path to a genre pack directory (containing projection.yaml)",
    )
    args = parser.parse_args(argv)

    genre_dir: Path = args.genre_dir
    if not genre_dir.is_dir():
        print(f"ERROR: {genre_dir} is not a directory", file=sys.stderr)
        return 2

    projection_yaml = genre_dir / "projection.yaml"
    if not projection_yaml.exists():
        print(f"No projection.yaml in {genre_dir} — no projection rules configured.")
        return 0

    try:
        rules = load_rules_from_yaml_path(projection_yaml)
        validate_projection_rules(rules)
    except ValidationError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    # Audit table
    print(f"{'KIND':<20} {'TYPE':<14} {'FIELD':<24} {'PREDICATE':<20} {'MASK'}")
    print("-" * 90)
    for rule in rules.rules:
        if isinstance(rule, TargetOnlyRule):
            print(
                f"{rule.kind:<20} {'target_only':<14} "
                f"{rule.target_only.field:<24} {'':<20} {''}"
            )
        elif isinstance(rule, IncludeIfRule):
            print(
                f"{rule.kind:<20} {'include_if':<14} "
                f"{'':<24} {rule.include_if.predicate:<20} {''}"
            )
        elif isinstance(rule, RedactFieldsRule):
            for spec in rule.redact_fields:
                print(
                    f"{rule.kind:<20} {'redact':<14} {spec.field:<24} "
                    f"{spec.unless.predicate:<20} {spec.mask!r}"
                )

    return 0


if __name__ == "__main__":
    sys.exit(main())
