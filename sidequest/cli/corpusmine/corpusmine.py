from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sidequest.corpus.miner import mine_save
from sidequest.corpus.writer import write_pairs


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="corpusmine",
        description="Mine (input, output) training pairs from a save.db.",
    )
    p.add_argument("--save", required=True, type=Path, help="Path to a save.db")
    p.add_argument("--out", required=True, type=Path, help="Output JSONL path")
    args = p.parse_args(argv)

    if not args.save.exists():
        print(f"error: save not found: {args.save}", file=sys.stderr)
        return 2

    pairs = list(mine_save(args.save))
    write_pairs(args.out, pairs)
    print(f"wrote {len(pairs)} pairs to {args.out}")
    return 0
