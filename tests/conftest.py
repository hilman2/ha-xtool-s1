"""Shared pytest fixtures for the xtool_s1 test suite."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import contextlib
import json
from pathlib import Path
from typing import Any

from aiohttp import WSMsgType, web
from homeassistant.const import CONF_HOST
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import pytest_socket

from custom_components.xtool_s1.const import DOMAIN

from .const import MOCK_HOST, MOCK_SERIAL

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> dict[str, Any]:
    """Load a JSON fixture from ``tests/fixtures/<name>``."""
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Auto-enable loading of the xtool_s1 custom component in every test."""
    return


@pytest.fixture(autouse=True)
def allow_loopback_sockets():
    """pytest-socket blocks all sockets by default in HA's test stack.

    Our fake S1 servers bind to 127.0.0.1, so we re-enable sockets for
    every test. We rely on aiohttp/asyncio to keep traffic local.
    """
    pytest_socket.enable_socket()


# --- fake S1 WebSocket server ----------------------------------------------


class FakeS1Server:
    """A minimal aiohttp app that emulates the S1 WebSocket protocol.

    The server responds to ``M2003`` requests with the configured snapshot
    JSON, replies to ``M303`` pings with a fixed position frame, and
    records every received frame for assertion in tests.

    Two test-only modes alter the reply behaviour:

    * ``silent`` — never reply. Used to test connect/probe timeouts.
    * ``broken_json`` — reply with a malformed M2003 body. Used to test
      that the listener doesn't crash on garbage frames.
    """

    def __init__(
        self,
        snapshot: dict[str, Any],
        *,
        silent: bool = False,
        broken_json: bool = False,
    ) -> None:
        self.snapshot = snapshot
        self.silent = silent
        self.broken_json = broken_json
        self.received: list[str] = []
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self.host = "127.0.0.1"
        self.port = 0
        self._connections: set[web.WebSocketResponse] = set()

    @property
    def m2003_text(self) -> str:
        return "M2003" + json.dumps(self.snapshot)

    async def start(self) -> None:
        app = web.Application()
        app.router.add_route("GET", "/", self._handle_ws)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        self._site = web.TCPSite(self._runner, self.host, 0)
        await self._site.start()
        # Resolve the actually-bound port.
        server = self._site._server
        sock = server.sockets[0]  # type: ignore[union-attr]
        self.port = sock.getsockname()[1]

    async def stop(self) -> None:
        for ws in list(self._connections):
            with contextlib.suppress(Exception):
                await ws.close()
        if self._runner is not None:
            await self._runner.cleanup()
        self._runner = None
        self._site = None

    async def push(self, frame: str) -> None:
        """Send a raw text frame to all connected clients."""
        for ws in list(self._connections):
            if not ws.closed:
                await ws.send_str(frame)

    async def push_binary(self, payload: bytes) -> None:
        """Send a raw binary frame to all connected clients."""
        for ws in list(self._connections):
            if not ws.closed:
                await ws.send_bytes(payload)

    async def close_all(self) -> None:
        """Close every active client connection from the server side."""
        for ws in list(self._connections):
            if not ws.closed:
                await ws.close()

    async def _handle_ws(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self._connections.add(ws)
        try:
            async for msg in ws:
                if msg.type != WSMsgType.TEXT:
                    continue
                text = msg.data.strip()
                self.received.append(text)
                if self.silent:
                    continue
                if text.startswith("M2003"):
                    if self.broken_json:
                        await ws.send_str("M2003{not json")
                    else:
                        await ws.send_str(self.m2003_text)
                elif text.startswith("M303"):
                    await ws.send_str("M303 X1.000 Y2.000")
        finally:
            self._connections.discard(ws)
        return ws


@pytest.fixture
async def fake_s1_server() -> AsyncIterator[FakeS1Server]:
    """Yield a started FakeS1Server bound to localhost on a random port."""
    server = FakeS1Server(load_fixture("m2003_idle.json"))
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
async def fake_s1_server_running() -> AsyncIterator[FakeS1Server]:
    """Variant of FakeS1Server pre-loaded with the running snapshot."""
    server = FakeS1Server(load_fixture("m2003_running.json"))
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
async def fake_s1_server_alarm() -> AsyncIterator[FakeS1Server]:
    """Variant of FakeS1Server pre-loaded with an alarm snapshot."""
    server = FakeS1Server(load_fixture("m2003_alarm.json"))
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
async def fake_s1_server_silent() -> AsyncIterator[FakeS1Server]:
    """A FakeS1Server that accepts WS but never replies. Used for timeout tests."""
    server = FakeS1Server(load_fixture("m2003_idle.json"), silent=True)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


@pytest.fixture
async def fake_s1_server_broken_json() -> AsyncIterator[FakeS1Server]:
    """A FakeS1Server that responds to M2003 with garbage JSON."""
    server = FakeS1Server(load_fixture("m2003_idle.json"), broken_json=True)
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


# --- silent server (TCP only, never speaks WS) ------------------------------


class SilentTcpServer:
    """A TCP server that accepts a connection then immediately closes it.

    Used to simulate a port that is open but does NOT speak the S1
    WebSocket protocol — the discovery scan must reject it.
    """

    def __init__(self) -> None:
        self.host = "127.0.0.1"
        self.port = 0
        self._server: asyncio.AbstractServer | None = None

    async def start(self) -> None:
        async def _handler(reader, writer):
            writer.close()
            await writer.wait_closed()

        self._server = await asyncio.start_server(_handler, self.host, 0)
        self.port = self._server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self._server = None


@pytest.fixture
async def silent_tcp_server() -> AsyncIterator[SilentTcpServer]:
    """Yield a started SilentTcpServer."""
    server = SilentTcpServer()
    await server.start()
    try:
        yield server
    finally:
        await server.stop()


# --- config entry helpers ---------------------------------------------------


@pytest.fixture
def mock_config_entry() -> MockConfigEntry:
    """Build a MockConfigEntry pre-populated with the mock host and serial."""
    return MockConfigEntry(
        domain=DOMAIN,
        title=f"xTool S1 ({MOCK_HOST})",
        unique_id=MOCK_SERIAL,
        data={CONF_HOST: MOCK_HOST},
    )
