"""Bounded failures when a socket returns malformed data or stops responding."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiohttp import WSMsgType

from custom_components.t3_code.api import T3Error, T3ProtocolError, T3Rpc


@pytest.mark.parametrize("message", ["{bad-json", "[]", '{"_tag":"Defect"}'])
async def test_invalid_message(message):
    socket = SimpleNamespace(
        send_json=AsyncMock(),
        receive=AsyncMock(return_value=SimpleNamespace(type=WSMsgType.TEXT, data=message)),
    )
    with pytest.raises(T3ProtocolError):
        await T3Rpc(socket).call("server.getUsageSummary", {})


async def test_timeout_is_recoverable_t3_error():
    socket = SimpleNamespace(send_json=AsyncMock(), receive=AsyncMock(side_effect=TimeoutError))
    with pytest.raises(T3Error, match="timed out"):
        await T3Rpc(socket).call("server.refreshProviders", {})


async def test_closed_socket():
    socket = SimpleNamespace(
        send_json=AsyncMock(),
        receive=AsyncMock(return_value=SimpleNamespace(type=WSMsgType.CLOSED)),
    )
    with pytest.raises(T3Error, match="closed"):
        await T3Rpc(socket).call("server.refreshProviders", {})
