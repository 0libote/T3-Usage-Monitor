"""Calendar requests and reset timestamps across midnight, month ends, and DST."""

from datetime import UTC, datetime

import pytest
from aiohttp import ClientSession

from custom_components.t3_code.api import T3Client
from custom_components.t3_code.coordinator import fetch_snapshot
from custom_components.t3_code.models import normalize


@pytest.mark.parametrize(
    ("stamp", "day", "month", "offset"),
    [
        ("2026-11-01T03:59:59Z", "2026-10-31", "2026-10-01", "-04:00"),
        ("2026-11-01T04:00:00Z", "2026-11-01", "2026-11-01", "-04:00"),
        ("2026-11-01T06:00:00Z", "2026-11-01", "2026-11-01", "-04:00"),
        ("2026-11-02T05:00:00Z", "2026-11-02", "2026-11-01", "-05:00"),
        ("2026-03-08T07:00:00Z", "2026-03-08", "2026-03-01", "-05:00"),
        ("2027-01-01T05:00:00Z", "2027-01-01", "2027-01-01", "-05:00"),
    ],
)
async def test_calendar_boundaries(t3_server, freezer, stamp, day, month, offset):
    freezer.move_to(stamp)
    t3_server["summary"]["buckets"][0]["day"] = day
    t3_server["providers"][0]["usageLimits"]["checkedAt"] = stamp
    async with ClientSession() as session:
        snapshot = await fetch_snapshot(
            T3Client(session, t3_server["url"], "session-secret"),
            "America/New_York",
            "test-server",
        )
    request = dict(t3_server["calls"])["server.getUsageSummary"]
    assert request["sinceDay"] == month
    assert request["untilDay"] == day
    reading = snapshot.readings["usage/all/today/tokens"]
    assert reading.value == 370
    assert reading.last_reset.isoformat() == f"{day}T00:00:00{offset}"


def test_rollover_does_not_include_previous_month(descriptor, summary):
    now = datetime(2026, 11, 1, tzinfo=UTC)
    readings = normalize(descriptor, summary, [], now.date(), "UTC", now).readings
    assert readings["usage/all/today/tokens"].value == 0
    assert readings["usage/all/month/tokens"].value == 0
    assert readings["usage/all/month/tokens"].last_reset == now
