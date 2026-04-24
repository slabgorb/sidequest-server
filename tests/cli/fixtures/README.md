# Group D CLI test fixtures

Hand-written SQLite save.db fixtures for the corpus-mining CLI tests (mine_save,
diff_per_player, etc.). These `.db` files are the **only** save databases the
Group D tests are allowed to read or mutate — the real `~/.sidequest/saves/`
tree is strictly off-limits. To regenerate (for example, after editing one of
the `.sql` sources to add new rounds or change row content), run
`uv run python tests/cli/fixtures/mint_fixtures.py` from the worktree root; the
minter deletes each existing `.db` and rebuilds it deterministically from the
paired `.sql` script, and the resulting `.db` files are committed alongside the
sources so the suite is self-contained without a conftest autouse hook.
