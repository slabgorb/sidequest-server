"""Entry point for python -m sidequest.cli.encountergen."""

from __future__ import annotations

import sys

from sidequest.cli.encountergen.encountergen import main

if __name__ == "__main__":
    sys.exit(main())
