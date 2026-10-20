"""Verify real HTTP/WebSocket framing, pairing, identity checks, and failures."""

import pytest
from aiohttp import ClientSession, web

from custom_components.t3_code.api import (
    T3AuthError,
    T3Client,
    T3Error,
    T3ProtocolError,
    parse_connection,
)
from custom_components.t3_code.coordinator import fetch_snapshot


@pytest.mark.parametrize(
    ("address", "credential", "expected"),
    [
        ("https://example.test/pair#token=abc%2B123", "", ("https://example.test", "abc+123")),
        ("http://host:3773/pair?token=abc", "", ("http://host:3773", "abc")),
        ("ws://host:3773/ws", "bearer", ("http://host:3773", "bearer")),
        ("https://HOST/", "override", ("https://host", "override")),
    ],
)
def test_connections(address, credential, expected):
    assert parse_connection(address, credential) == expected


@pytest.mark.parametrize(
    "address",
    [
        "file:///etc/passwd",
        "https://user:secret@host",
        "https://host/unrelated",
        "https://host:invalid",
        "not-a-url",
        "https://host",
    ],
)
def test_invalid_connections(address):
    with pytest.raises(ValueError):
        parse_connection(address)


async def test_pair_and_metrics(t3_server):
    async with ClientSession() as session:
        client = T3Client(session, t3_server["url"])
        assert await client.pair("pair-secret") == "session-secret"
        snapshot = await fetch_snapshot(client, "Pacific/Auckland", "test-server")
    assert snapshot.readings["limit/codex/primary/remaining"].value == 75
    calls = dict(t3_server["calls"])
    assert set(calls) == {"server.getUsageSummary", "server.refreshProviders"}
    assert calls["server.getUsageSummary"]["timeZone"] == "Pacific/Auckland"


async def test_identity_checked_before_sending_token(t3_server):
    async with ClientSession() as session:
        client = T3Client(session, t3_server["url"], "session-secret")
        with pytest.raises(T3AuthError, match="different T3 server"):
            await fetch_snapshot(client, "UTC", "different-id")
    assert not t3_server["calls"]


@pytest.mark.parametrize("failure", ["auth_failure", "rpc_denied"])
async def test_auth_failures_are_safe(t3_server, failure):
    t3_server[failure] = True
    async with ClientSession() as session:
        client = T3Client(session, t3_server["url"], "session-secret")
        with pytest.raises(T3AuthError) as error:
            await fetch_snapshot(client, "UTC", "test-server")
    assert "secret" not in str(error.value)


async def test_failed_probe_preserves_usage_but_disables_limits(t3_server):
    t3_server["refresh_failure"] = True
    async with ClientSession() as session:
        snapshot = await fetch_snapshot(
            T3Client(session, t3_server["url"], "session-secret"), "UTC", "test-server"
        )
    assert snapshot.readings["usage/all/month/tokens"].value == 370
    assert snapshot.readings["limit/codex/primary/used"].value is None
    assert snapshot.readings["limits_status"].value == "failed"


async def test_pairing_rejection(t3_server):
    async with ClientSession() as session:
        client = T3Client(session, t3_server["url"])
        with pytest.raises(T3AuthError):
            await client.pair("wrong")


async def test_redirect_never_forwards_pairing_secret(aiohttp_server, t3_server):
    async def redirect(request):
        raise web.HTTPTemporaryRedirect(location=t3_server["url"] + "/oauth/token")

    app = web.Application()
    app.router.add_post("/oauth/token", redirect)
    server = await aiohttp_server(app)
    url = str(server.make_url("")).rstrip("/")
    async with ClientSession() as session:
        client = T3Client(session, url)
        with pytest.raises(T3Error):
            await client.pair("pair-secret")
    assert t3_server["pair_count"] == 0


async def test_unknown_protocol(t3_server):
    t3_server["descriptor"]["orchestrationProtocolVersion"] = 999
    async with ClientSession() as session:
        client = T3Client(session, t3_server["url"])
        with pytest.raises(T3ProtocolError):
            await client.descriptor()
