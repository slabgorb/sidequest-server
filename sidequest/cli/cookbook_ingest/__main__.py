"""Entry point for python -m sidequest.cli.cookbook_ingest."""

from __future__ import annotations

import sys

from sidequest.cli.cookbook_ingest.ingest import main

if __name__ == "__main__":
    sys.exit(main())
