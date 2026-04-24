from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from sidequest.corpus.schema import LabeledPair, TrainingPair

DEFAULT_PORT = 9865
_STATIC = Path(__file__).parent / "static"


def _load_unlabeled(corpus: Path) -> list[TrainingPair]:
    return [
        TrainingPair.model_validate_json(line)
        for line in corpus.read_text().splitlines()
        if line.strip()
    ]


def build_app(corpus: Path, labeled_out: Path) -> FastAPI:
    if not corpus.exists():
        raise FileNotFoundError(f"corpus not found: {corpus}")
    labeled_out.parent.mkdir(parents=True, exist_ok=True)

    app = FastAPI(title="SideQuest Corpus Labeler", version="0.1.0")
    app.mount("/static", StaticFiles(directory=_STATIC), name="static")

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
            labeled = sum(1 for line in labeled_out.read_text().splitlines() if line.strip())
        return {"unlabeled": unlabeled, "labeled": labeled}

    @app.post("/api/label")
    def label(pair: LabeledPair) -> dict[str, bool]:
        with labeled_out.open("a", encoding="utf-8") as fh:
            fh.write(pair.model_dump_json())
            fh.write("\n")
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
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0
