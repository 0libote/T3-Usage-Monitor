"""Single-socket polling, handshake edge cases, and deterministic midnight tests."""

from contextlib import asynccontextmanager
from datetime import datetime

import pytest
from aiohttp import ClientSession

from custom_components.t3_code.api import T3AuthError, T3Client, T3Error, _check_status
from custom_components.t3_code.coordinator import fetch_snapshot


async def test_common_path_opens_one_websocket(t3_server, monkeypatch):
    """Usage and quota refresh share a single RPC session on the happy path."""
    opens = []
    real_rpc = T3Client.rpc

    @asynccontextmanager
    async def counting_rpc(self, descriptor):
        opens.append(descriptor)
        async with real_rpc(self, descriptor) as rpc:
            yield rpc

    monkeypatch.setattr(T3Client, "rpc", counting_rpc)
    async with ClientSession() as session:
        snapshot = await fetch_snapshot(
            T3Client(session, t3_server["url"], "session-secret"), "UTC", "test-server"
        )
    assert len(opens) == 1
    assert snapshot.readings["usage/all/month/tokens"].value == 370
    assert snapshot.readings["limit/codex/primary/remaining"].value == 75
    methods = {method for method, _ in t3_server["calls"]}
    assert methods == {"server.getUsageSummary", "server.refreshProviders"}


def test_check_status_none_is_generic_error():
    """A refused handshake without an HTTP status must not raise TypeError."""
    with pytest.raises(T3Error):
        _check_status(None)
    with pytest.raises(T3AuthError):
        _check_status(401)
    with pytest.raises(T3AuthError):
        _check_status(403)
    with pytest.raises(T3Error):
        _check_status(500)
    _check_status(200)


@pytest.mark.parametrize(
    ("stamp", "day", "month"),
    [
        ("2026-11-01T03:59:59Z", "2026-10-31", "2026-10-01"),
        ("2026-11-01T04:00:30Z", "2026-11-01", "2026-11-01"),
    ],
)
async def test_midnight_straddle_single_instant(t3_server, stamp, day, month):
    """An injected instant drives the request window and snapshot without clock reads."""
    t3_server["summary"]["buckets"][0]["day"] = day
    t3_server["providers"][0]["usageLimits"]["checkedAt"] = stamp
    async with ClientSession() as session:
        snapshot = await fetch_snapshot(
            T3Client(session, t3_server["url"], "session-secret"),
            "America/New_York",
            "test-server",
            now=datetime.fromisoformat(stamp),
        )
    request = dict(t3_server["calls"])["server.getUsageSummary"]
    assert request["sinceDay"] == month
    assert request["untilDay"] == day
    reading = snapshot.readings["usage/all/today/tokens"]
    assert reading.value == 370
    assert reading.last_reset.date().isoformat() == day
    assert snapshot.readings["limit/codex/primary/remaining"].value == 75
