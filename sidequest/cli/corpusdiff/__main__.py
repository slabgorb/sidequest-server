"""Entry point for python -m sidequest.cli.corpusdiff."""

from __future__ import annotations

import sys

from sidequest.cli.corpusdiff.corpusdiff import main

if __name__ == "__main__":
    sys.exit(main())
