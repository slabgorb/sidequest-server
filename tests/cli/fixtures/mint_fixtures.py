"""Mint Group D corpus-mining test fixtures.

Idempotently builds three small SQLite save.db files next to this script from
the hand-written .sql source files. These fixtures are the only save.db files
the Group D CLI tests are allowed to read or mutate — the real
``~/.sidequest/saves/`` tree is off-limits.

Usage::

    uv run python tests/cli/fixtures/mint_fixtures.py

The script deletes any existing ``*.db`` before recreating it from the paired
``.sql`` source, so the output is reproducible from the checked-in SQL. The
generated ``.db`` files are committed alongside the ``.sql`` sources so the
test suite is self-contained (see README.md in this directory).
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

FIXTURES_DIR = Path(__file__).resolve().parent

# (db_name, sql_name) — order is load-bearing for logging only.
FIXTURE_PAIRS: list[tuple[str, str]] = [
    ("single_session.db", "single_session.sql"),
    ("per_player_a.db", "per_player_a.sql"),
    ("per_player_b.db", "per_player_b.sql"),
]


def mint_one(db_path: Path, sql_path: Path) -> None:
    """Mint a single fixture: delete db, executescript from sql, commit."""
    if not sql_path.exists():
        raise FileNotFoundError(f"Missing SQL source: {sql_path}")

    # Remove the .db and any WAL/SHM sidecars from a previous run.
    for suffix in ("", "-wal", "-shm", "-journal"):
        sibling = db_path.with_name(db_path.name + suffix)
        if sibling.exists():
            sibling.unlink()

    sql = sql_path.read_text()
    conn = sqlite3.connect(db_path)
    try:
        # Journal=DELETE keeps the fixture as a single file on disk (no -wal).
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()


def main() -> int:
    for db_name, sql_name in FIXTURE_PAIRS:
        db_path = FIXTURES_DIR / db_name
        sql_path = FIXTURES_DIR / sql_name
        mint_one(db_path, sql_path)
        print(f"minted {db_path.relative_to(FIXTURES_DIR.parent.parent.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
