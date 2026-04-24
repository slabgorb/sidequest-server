"""Entry point for python -m sidequest.cli.corpuslabel."""
from __future__ import annotations

import sys

from sidequest.cli.corpuslabel.corpuslabel import main

if __name__ == "__main__":
    sys.exit(main())
