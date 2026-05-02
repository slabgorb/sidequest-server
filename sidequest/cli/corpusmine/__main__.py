"""Entry point for python -m sidequest.cli.corpusmine."""

from __future__ import annotations

import sys

from sidequest.cli.corpusmine.corpusmine import main

if __name__ == "__main__":
    sys.exit(main())
