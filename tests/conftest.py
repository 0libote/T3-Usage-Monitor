"""Shared T3 fixtures and Home Assistant test setup."""

from datetime import UTC, datetime

import pytest
from aiohttp import web


@pytest.fixture(autouse=True)
def custom_integrations(enable_custom_integrations):
    """Allow the integration under test to load."""


@pytest.fixture
def descriptor():
    return {
        "environmentId": "test-server",
        "label": "Development",
        "serverVersion": "0.1.0",
        "orchestrationProtocolVersion": 2,
        "platform": {"os": "linux", "arch": "x64"},
        "capabilities": {},
    }


@pytest.fixture
def summary():
    return {
        "contractVersion": 6,
        "readAt": "2026-10-19T12:00:00Z",
        "timeZone": "UTC",
        "sinceDay": "2026-10-01",
        "untilDay": "2026-10-19",
        "buckets": [
            {
                "day": "2026-10-19",
                "provider": "codex",
                "model": "test-model",
                "totals": {
                    "uncachedInputTokens": 100,
                    "cachedInputTokens": 200,
                    "cacheCreationTokens": 20,
                    "outputTokens": 50,
                    "reasoningTokens": 30,
                },
                "costUsd": 0.01,
                "cacheSavingsUsd": 0.002,
                "unpricedRecords": 0,
            }
        ],
        "sources": [{"status": "ok"}],
        "pricing": {"status": "fresh"},
    }


@pytest.fixture
def providers():
    return [
        {
            "instanceId": "codex",
            "driver": "codex",
            "displayName": "Codex",
            "usageLimits": {
                "checkedAt": "2026-10-19T12:00:00Z",
                "windows": [
                    {
                        "id": "primary",
                        "kind": "session",
                        "label": "5 hour",
                        "usedPercent": 25,
                        "resetsAt": "2099-01-01T00:00:00Z",
                    }
                ],
                "resetCredits": {"availableCount": 2},
            },
        }
    ]


@pytest.fixture
def now():
    return datetime(2026, 10, 19, 12, tzinfo=UTC)


@pytest.fixture
async def t3_server(aiohttp_server, descriptor, summary, providers, socket_enabled, freezer):
    """An actual HTTP/WebSocket server implementing T3's captured contracts."""
    freezer.move_to("2026-10-19T12:00:00Z")
    calls = []
    state = {
        "token": "session-secret",
        "pair_count": 0,
        "auth_failure": False,
        "rpc_denied": False,
        "refresh_failure": False,
        "summary_failure": False,
        "descriptor_failure": False,
        "failed_methods": set(),
        "closed_methods": set(),
        "descriptor": descriptor,
        "summary": summary,
        "providers": providers,
    }

    async def environment(request):
        if state["descriptor_failure"]:
            raise web.HTTPServiceUnavailable()
        return web.json_response(state["descriptor"])

    async def token(request):
        form = await request.post()
        assert form["scope"] == "orchestration:read diagnostics:read"
        assert form["subject_token_type"] == "urn:t3:params:oauth:token-type:environment-bootstrap"
        if form["subject_token"] != "pair-secret" or state["pair_count"]:
            return web.json_response({"error": "invalid_grant"}, status=400)
        state["pair_count"] += 1
        return web.json_response(
            {
                "access_token": state["token"],
                "token_type": "Bearer",
                "expires_in": 86400,
            }
        )

    async def websocket(request):
        assert request.query["orchestrationProtocol"] == str(
            state["descriptor"]["orchestrationProtocolVersion"]
        )
        if (
            state["auth_failure"]
            or request.headers.get("Authorization") != f"Bearer {state['token']}"
        ):
            raise web.HTTPUnauthorized()
        socket = web.WebSocketResponse()
        await socket.prepare(request)
        async for message in socket:
            data = message.json()
            if data["_tag"] == "Pong":
                continue
            method = data["tag"]
            calls.append((method, data["payload"]))
            if method in state["closed_methods"]:
                await socket.close()
                break
            if state["rpc_denied"]:
                result = {
                    "_tag": "Failure",
                    "cause": [
                        {
                            "_tag": "Fail",
                            "error": {
                                "_tag": "EnvironmentAuthorizationError",
                                "detail": "secret-do-not-log",
                            },
                        }
                    ],
                }
            elif (
                method in state["failed_methods"]
                or (method == "server.refreshProviders" and state["refresh_failure"])
                or (method == "server.getUsageSummary" and state["summary_failure"])
            ):
                result = {
                    "_tag": "Failure",
                    "cause": [
                        {
                            "_tag": "Fail",
                            "error": {
                                "_tag": "UsageReadError",
                                "detail": "secret-do-not-log",
                            },
                        }
                    ],
                }
            else:
                value = {
                    "server.getUsageSummary": state["summary"],
                    "server.getConfig": {"providers": state["providers"]},
                    "server.refreshProviders": {"providers": state["providers"]},
                }[method]
                result = {"_tag": "Success", "value": value}
            # T3 uses newline-delimited JSON and may coalesce a Ping with an Exit.
            import json

            await socket.send_str(
                json.dumps({"_tag": "Ping"})
                + "\n"
                + json.dumps(
                    {
                        "_tag": "Exit",
                        "requestId": data["id"],
                        "exit": result,
                    }
                )
                + "\n"
            )
        return socket

    app = web.Application()
    app.router.add_get("/.well-known/t3/environment", environment)
    app.router.add_post("/oauth/token", token)
    app.router.add_get("/ws", websocket)
    server = await aiohttp_server(app)
    state["url"] = str(server.make_url("")).rstrip("/")
    state["calls"] = calls
    return state
