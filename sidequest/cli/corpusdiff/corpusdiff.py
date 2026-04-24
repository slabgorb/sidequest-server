from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict
from pathlib import Path

from sidequest.corpus.diff import diff_per_player


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="corpusdiff",
        description="Diff narrator content across per-player save.db files for the same world.",
    )
    p.add_argument(
        "--save",
        action="append",
        required=True,
        type=Path,
        dest="saves",
        help="Per-player save.db (specify at least twice)",
    )
    p.add_argument("--out", required=True, type=Path, help="Output JSON path")
    args = p.parse_args(argv)

    if len(args.saves) < 2:
        print(
            "error: --save must be given at least twice (nothing to diff)",
            file=sys.stderr,
        )
        return 2

    missing = [str(s) for s in args.saves if not s.exists()]
    if missing:
        print(f"error: saves not found: {', '.join(missing)}", file=sys.stderr)
        return 2

    try:
        divergences = [asdict(d) for d in diff_per_player(saves=args.saves)]
    except sqlite3.DatabaseError as e:
        print(f"error: not a valid sqlite save: {e}", file=sys.stderr)
        return 2
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(divergences, indent=2))
    print(f"wrote {len(divergences)} divergences to {args.out}")
    return 0
