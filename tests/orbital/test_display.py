"""format_duration tests — scale-appropriate display per spec §3.1."""

from __future__ import annotations

import pytest

from sidequest.orbital.display import format_duration


@pytest.mark.parametrize(
    "hours, expected",
    [
        (0.5, "30 minutes"),
        (0.0167, "1 minute"),
        (0.99, "59 minutes"),
        (1.0, "1 hour"),
        (5.0, "5 hours"),
        (23.999, "24 hours"),
        (24.0, "1 day"),
        (48.0, "2 days"),
        (72.0, "3 days"),
        (336.0, "14 days"),  # threshold: still days
        (337.0, "2 weeks"),  # threshold: switches to weeks
        (504.0, "3 weeks"),  # 21 days
        (2160.0, "13 weeks"),  # 90 days
        (2161.0, "3 months"),  # 90+ → months
        (8760.0, "12 months"),  # 1 year — formatter chooses months
        (17520.0, "2 years"),  # 2+ years → years
    ],
)
def test_format_duration(hours, expected):
    assert format_duration(hours) == expected


def test_negative_rejected():
    with pytest.raises(ValueError, match="negative"):
        format_duration(-1.0)
