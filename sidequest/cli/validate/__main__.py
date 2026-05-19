"""Entry point: ``python -m sidequest.cli.validate <subcommand>``.

Dispatches to one of:

* ``locations`` — Story 54-3 location-manifest validator.
* ``projection-check`` — projection.yaml audit (legacy single-genre).

Direct module entry remains available for backwards compatibility:
``python -m sidequest.cli.validate.projection_check <genre_dir>``.
"""

from __future__ import annotations

import sys

import click

from sidequest.cli.validate.locations import main as locations_main
from sidequest.cli.validate.projection_check import main as projection_check_main


@click.group()
def cli() -> None:
    """SideQuest content validators."""


# ``locations`` is a click.command — register the underlying object directly.
cli.add_command(locations_main, name="locations")


@cli.command(name="projection-check")
@click.argument("genre_dir")
def projection_check(genre_dir: str) -> None:
    """Audit a genre pack's projection.yaml."""
    sys.exit(projection_check_main([genre_dir]))


if __name__ == "__main__":
    cli()
