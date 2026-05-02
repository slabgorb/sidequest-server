"""Entry point for python -m sidequest.cli.namegen."""

from __future__ import annotations

import sys

from sidequest.cli.namegen.namegen import main

if __name__ == "__main__":
    sys.exit(main())
