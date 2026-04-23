from datetime import date

import pytest

from sidequest.game.game_slug import InvalidSlugError, generate_slug, parse_slug


def test_generate_slug_uses_date_and_world():
    assert generate_slug(world_slug="moldharrow-keep", today=date(2026, 4, 22)) == "2026-04-22-moldharrow-keep"


def test_generate_slug_rejects_empty_world():
    with pytest.raises(ValueError):
        generate_slug(world_slug="", today=date(2026, 4, 22))


def test_parse_slug_roundtrip():
    parsed = parse_slug("2026-04-22-moldharrow-keep")
    assert parsed.date == date(2026, 4, 22)
    assert parsed.world_slug == "moldharrow-keep"


def test_parse_slug_world_with_dashes():
    parsed = parse_slug("2026-12-01-the-iron-city")
    assert parsed.world_slug == "the-iron-city"


def test_parse_slug_rejects_missing_date():
    with pytest.raises(InvalidSlugError):
        parse_slug("moldharrow-keep")


def test_parse_slug_rejects_malformed_date():
    with pytest.raises(InvalidSlugError):
        parse_slug("2026-13-40-moldharrow")
