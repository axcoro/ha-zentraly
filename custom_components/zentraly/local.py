"""Local WebSocket transport for Zentraly thermostats."""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import json
from typing import Any

import aiohttp


LOCAL_COMMAND_TIMEOUT_SECONDS = 5

LocalResolver = Callable[[str], Awaitable[tuple[str, int] | None]]


class ZentralyLocalError(Exception):
    """Local discovery or communication error."""


class ZentralyLocalClient:
    """Send the same device commands as the official app over the LAN."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        resolver: LocalResolver,
    ) -> None:
        """Initialize the local client."""
        self._session = session
        self._resolver = resolver
        self._address_cache: dict[str, tuple[str, int]] = {}
        self._rid = 0

    def _next_rid(self) -> int:
        rid = self._rid
        self._rid = (rid + 1) % 10000
        return rid

    async def _resolve(self, device_serial: str) -> tuple[str, int]:
        if cached := self._address_cache.get(device_serial):
            return cached

        resolved = await self._resolver(device_serial)
        if resolved is None:
            raise ZentralyLocalError("device not found via mDNS")

        self._address_cache[device_serial] = resolved
        return resolved

    @staticmethod
    def _websocket_url(host: str, port: int) -> str:
        formatted_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        return f"ws://{formatted_host}:{port}/ws"

    @staticmethod
    def _decode_payload(payload: Any) -> dict[str, Any]:
        if isinstance(payload, str):
            payload = json.loads(payload)
        if not isinstance(payload, dict):
            raise ZentralyLocalError("device returned a non-object response")

        for key in ("data", "ioData", "payload"):
            nested = payload.get(key)
            if isinstance(nested, str):
                try:
                    nested = json.loads(nested)
                except json.JSONDecodeError:
                    continue
            if isinstance(nested, dict):
                return {**payload, **nested}

        return payload

    async def _receive_response(
        self,
        websocket: aiohttp.ClientWebSocketResponse,
        rid: int,
    ) -> dict[str, Any]:
        async with asyncio.timeout(LOCAL_COMMAND_TIMEOUT_SECONDS):
            while True:
                message = await websocket.receive()
                if message.type == aiohttp.WSMsgType.TEXT:
                    response = self._decode_payload(message.data)
                    response_rid = response.get("rid")
                    if response_rid is None or response_rid == rid:
                        return response
                    continue
                if message.type == aiohttp.WSMsgType.BINARY:
                    response = self._decode_payload(message.data.decode())
                    response_rid = response.get("rid")
                    if response_rid is None or response_rid == rid:
                        return response
                    continue
                if message.type in (
                    aiohttp.WSMsgType.CLOSE,
                    aiohttp.WSMsgType.CLOSED,
                    aiohttp.WSMsgType.ERROR,
                ):
                    raise ZentralyLocalError("device closed the WebSocket")

    @staticmethod
    def _validate_response(response: dict[str, Any]) -> dict[str, Any]:
        status = response.get("status")
        if status != 200:
            raise ZentralyLocalError(f"device status {status}")
        return response

    async def send_command(
        self,
        device_serial: str,
        key: str,
        command: str,
        data: dict[str, Any],
    ) -> dict[str, Any]:
        """Authenticate locally and send one device command."""
        host, port = await self._resolve(device_serial)

        try:
            async with self._session.ws_connect(
                self._websocket_url(host, port),
                timeout=LOCAL_COMMAND_TIMEOUT_SECONDS,
            ) as websocket:
                login_rid = self._next_rid()
                await websocket.send_json(
                    {"cmd": "login", "rid": login_rid, "key": key}
                )
                self._validate_response(
                    await self._receive_response(websocket, login_rid)
                )

                command_rid = self._next_rid()
                await websocket.send_json(
                    {"cmd": command, "rid": command_rid, **data}
                )
                return self._validate_response(
                    await self._receive_response(websocket, command_rid)
                )
        except ZentralyLocalError:
            self._address_cache.pop(device_serial, None)
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError, json.JSONDecodeError) as err:
            self._address_cache.pop(device_serial, None)
            raise ZentralyLocalError(
                f"local command failed ({type(err).__name__}: {err})"
            ) from err
