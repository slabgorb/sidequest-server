from __future__ import annotations

import argparse
import logging
import sys
import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from sidequest.corpus.schema import LabeledPair, TrainingPair

DEFAULT_PORT = 9865
_STATIC = Path(__file__).parent / "static"
_log = logging.getLogger("sidequest.corpus.corpuslabel")


def _load_unlabeled(corpus: Path) -> list[TrainingPair]:
    out: list[TrainingPair] = []
    for line_no, line in enumerate(corpus.read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            out.append(TrainingPair.model_validate_json(line))
        except Exception as e:
            _log.warning("corpus line %d is malformed, skipping: %s", line_no, e)
    return out


def build_app(corpus: Path, labeled_out: Path) -> FastAPI:
    if not corpus.exists():
        raise FileNotFoundError(f"corpus not found: {corpus}")
    labeled_out.parent.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="SideQuest Corpus Labeler", version="0.1.0")
    app.mount("/static", StaticFiles(directory=_STATIC), name="static")

    # Per-app lock serialises concurrent /api/label writers so appends don't
    # interleave. One app instance per process, so one lock per process.
    _label_lock = threading.Lock()

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(_STATIC / "index.html")

    @app.get("/api/pairs")
    def pairs() -> list[dict]:
        return [p.model_dump() for p in _load_unlabeled(corpus)]

    @app.get("/api/count")
    def count() -> dict[str, int]:
        unlabeled = len(_load_unlabeled(corpus))
        labeled = 0
        if labeled_out.exists():
            for line_no, line in enumerate(labeled_out.read_text().splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    LabeledPair.model_validate_json(line)
                    labeled += 1
                except Exception as e:
                    _log.warning("labeled line %d is malformed, not counted: %s", line_no, e)
        return {"unlabeled": unlabeled, "labeled": labeled}

    @app.post("/api/label")
    def label(pair: LabeledPair) -> dict[str, bool]:
        # Build the full record as one bytes blob, then write with the lock
        # held so concurrent POSTs can't interleave their newlines.
        line = pair.model_dump_json() + "\n"
        with _label_lock, labeled_out.open("a", encoding="utf-8") as fh:
            fh.write(line)
        return {"ok": True}

    return app


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="corpuslabel",
        description="Standalone corpus labeling web UI.",
    )
    p.add_argument("corpus", type=Path, help="Path to mined JSONL corpus")
    p.add_argument(
        "--out",
        type=Path,
        default=Path.home() / ".sidequest" / "corpus" / "labeled.jsonl",
        help="Where to append LabeledPair rows (default: ~/.sidequest/corpus/labeled.jsonl)",
    )
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--host", default="127.0.0.1")
    args = p.parse_args(argv)

    try:
        app = build_app(corpus=args.corpus, labeled_out=args.out)
    except FileNotFoundError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    print(f"corpuslabel listening on http://{args.host}:{args.port}")
    print(f"corpus: {args.corpus}")
    print(f"labeled output: {args.out}")
    try:
        uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    except OSError as e:
        print(f"error: cannot bind {args.host}:{args.port}: {e}", file=sys.stderr)
        return 2
    return 0
