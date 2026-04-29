"""Magic test fixtures.

Importing ``sidequest.magic.plugins`` triggers the side-effect registration
of every shipped plugin into ``MAGIC_PLUGINS``. Tests need that side effect
before calling ``get_plugin``. Hoist it into a session-scoped autouse fixture
so individual test bodies don't have to repeat the import.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True, scope="session")
def _populate_magic_plugins_registry():
    import sidequest.magic.plugins  # noqa: F401
