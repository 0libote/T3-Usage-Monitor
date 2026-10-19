"""Small, read-only client for T3 Code's HTTP and Effect RPC contracts.

This module intentionally has no Home Assistant dependency. Authentication is
T3's bootstrap token exchange; usage is read through the same RPCs as its UI.
"""

import asyncio
import json
from contextlib import asynccontextmanager
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

from aiohttp import ClientError, ClientSession, WSMsgType, WSServerHandshakeError

from .const import SCOPES


class T3Error(Exception):
    """A safe-to-display error, never containing a credential or raw response."""


class T3AuthError(T3Error):
    """The credential is expired, revoked, or missing required scopes."""


class T3ProtocolError(T3Error):
    """The server does not speak a supported contract."""


def parse_connection(address: str, credential: str = "") -> tuple[str, str]:
    """Accept a server origin or T3's /pair#token=... link."""
    parts = urlsplit(address.strip())
    if (
        parts.scheme not in ("http", "https", "ws", "wss")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.path not in ("", "/", "/pair", "/ws")
    ):
        raise ValueError("Enter the server URL or a T3 pairing link")
    # Accessing port validates malformed port numbers too.
    _ = parts.port
    scheme = {"ws": "http", "wss": "https"}.get(parts.scheme, parts.scheme)
    embedded = parse_qs(parts.fragment).get("token", []) or parse_qs(parts.query).get("token", [])
    token = credential.strip() or (embedded[0].strip() if embedded else "")
    if not token:
        raise ValueError("A pairing or bearer token is required")
    return urlunsplit((scheme, parts.netloc.lower(), "", "", "")), token


def _check_status(status: int) -> None:
    if status in (401, 403):
        raise T3AuthError("T3 authentication failed; check the token and its read permissions")
    if status >= 400:
        raise T3Error("T3 rejected the HTTP request")


class T3Client:
    """Use a caller-owned aiohttp session without modifying its cookies."""

    def __init__(self, session: ClientSession, url: str, token: str = "") -> None:
        self.session = session
        self.url = url
        self.token = token

    async def descriptor(self) -> dict[str, Any]:
        """Discover identity and the WebSocket protocol without sending a secret."""
        try:
            async with (
                asyncio.timeout(15),
                self.session.get(
                    f"{self.url}/.well-known/t3/environment", allow_redirects=False
                ) as response,
            ):
                _check_status(response.status)
                data = await response.json()
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("environmentId"), str)
                or not data["environmentId"]
                or data.get("orchestrationProtocolVersion") not in (1, 2)
            ):
                raise T3ProtocolError("This server does not expose a supported T3 protocol")
            return data
        except (ClientError, TimeoutError, ValueError) as err:
            raise T3Error("Could not read the T3 server identity") from err

    async def pair(self, credential: str) -> str:
        """Exchange a one-time credential for a bearer session with read scopes."""
        form = {
            "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
            "subject_token": credential,
            "subject_token_type": "urn:t3:params:oauth:token-type:environment-bootstrap",
            "requested_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "scope": SCOPES,
            "client_label": "Home Assistant",
            "client_device_type": "bot",
            "client_os": "Home Assistant OS",
        }
        try:
            async with (
                asyncio.timeout(15),
                self.session.post(
                    f"{self.url}/oauth/token", data=form, allow_redirects=False
                ) as response,
            ):
                if response.status == 400:
                    raise T3AuthError(
                        "Pairing token is invalid, expired, used, or lacks read scopes"
                    )
                _check_status(response.status)
                data = await response.json()
            if not isinstance(data, dict) or not isinstance(data.get("access_token"), str):
                raise T3ProtocolError("T3 did not return a bearer session")
            if data.get("token_type") != "Bearer" or not data["access_token"]:
                raise T3ProtocolError("T3 did not return a bearer session")
            self.token = data["access_token"]
            return self.token
        except (ClientError, TimeoutError, ValueError) as err:
            raise T3Error("Could not exchange the T3 pairing token") from err

    @asynccontextmanager
    async def rpc(self, descriptor: dict[str, Any]):
        """Open a short-lived socket; close it on success, failure, or cancellation."""
        version = descriptor["orchestrationProtocolVersion"]
        # http(s) origins use ws(s) sockets; plain ws only arises from a user-supplied
        # http origin (typically loopback/LAN), wss from https. NOSONAR: not hardcoded.
        ws_url = self.url.replace("https://", "wss://", 1).replace(
            "http://", "ws://", 1
        )  # NOSONAR (python:S5332)
        try:
            async with (
                asyncio.timeout(180),
                self.session.ws_connect(
                    f"{ws_url}/ws?orchestrationProtocol={version}",
                    headers={"Authorization": f"Bearer {self.token}"},
                    max_msg_size=16 * 1024 * 1024,
                ) as socket,
            ):
                yield T3Rpc(socket)
        except WSServerHandshakeError as err:
            _check_status(err.status)
            raise T3Error("Could not open the T3 connection") from err
        except (ClientError, TimeoutError) as err:
            raise T3Error("T3 connection failed or timed out") from err


class T3Rpc:
    """Sequential Effect RPC requests with NDJSON framing and ping support."""

    def __init__(self, socket) -> None:
        self.socket = socket
        self.next_id = 0

    @staticmethod
    def _is_auth_failure(causes: Any) -> bool:
        """Check whether an Exit cause denies read permissions."""
        if isinstance(causes, dict):
            causes = [causes]
        if not isinstance(causes, list):
            return False
        return any(
            isinstance(cause, dict)
            and isinstance(cause.get("error"), dict)
            and cause["error"].get("_tag") == "EnvironmentAuthorizationError"
            for cause in causes
        )

    @staticmethod
    def _exit_value(data: dict[str, Any], request_id: str) -> tuple[bool, Any]:
        """Return (done, value) for an Exit frame, else (False, None) to skip."""
        if str(data.get("requestId")) != request_id or data.get("_tag") != "Exit":
            return False, None
        result = data.get("exit", {})
        if result.get("_tag") == "Success":
            return True, result.get("value")
        if T3Rpc._is_auth_failure(result.get("cause", [])):
            raise T3AuthError("T3 token lacks the required read permissions")
        raise T3Error("T3 could not complete the usage request")

    @staticmethod
    def _parse_frame(line: str) -> dict[str, Any]:
        """Decode one NDJSON line or raise a protocol error."""
        data = json.loads(line)
        if not isinstance(data, dict):
            raise T3ProtocolError("T3 returned an invalid RPC message")
        return data

    async def _receive_value(self, request_id: str) -> Any:
        """Read frames until this request's Exit, answering server pings."""
        while True:
            message = await self.socket.receive()
            if message.type != WSMsgType.TEXT:
                raise T3Error("T3 closed the connection before answering")
            for line in message.data.splitlines():
                if not line.strip():
                    continue
                data = self._parse_frame(line)
                if data.get("_tag") == "Ping":
                    await self.socket.send_json({"_tag": "Pong"})
                    continue
                if data.get("_tag") == "Defect":
                    raise T3ProtocolError("T3 could not decode the usage request")
                done, value = self._exit_value(data, request_id)
                if done:
                    return value

    async def call(self, method: str, payload: dict[str, Any]) -> Any:
        """Read one RPC result without starting a subscription or agent run."""
        self.next_id += 1
        request_id = str(self.next_id)
        await self.socket.send_json(
            {"_tag": "Request", "id": request_id, "tag": method, "payload": payload, "headers": []}
        )
        try:
            async with asyncio.timeout(90):
                return await self._receive_value(request_id)
        except TimeoutError as err:
            raise T3Error("T3 timed out while answering the usage request") from err
        except (ValueError, TypeError, AttributeError) as err:
            raise T3ProtocolError("T3 returned an invalid RPC response") from err
