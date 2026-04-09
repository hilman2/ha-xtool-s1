"""Shared pytest fixtures for the xtool_s1 test suite."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
import contextlib
import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from aiohttp import WSMsgType, web
from homeassistant.const import CONF_HOST
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
import pytest_socket

from custom_components.xtool_s1.const import DOMAIN

from .const import MOCK_HOST, MOCK_SERIAL

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@contextlib.contextmanager
def patch_ports(server: FakeS1Server) -> Iterator[None]:
    """Patch both WS_PORT and HTTP_PORT to point at a FakeS1Server.

    All entity-level tests need both ports redirected at the local
    fake server because the integration now uses HTTP for writes and
    WebSocket for reads.
    """
    with patch.multiple(
        "custom_components.xtool_s1.const",
        WS_PORT=server.port,
        HTTP_PORT=server.http_port,
    ):
        yield


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
        mac: str = "30:30:f9:71:7a:f4",
        subfirmware: str = "V40.32.013.2224.01 B1",
        http_fail: bool = False,
    ) -> None:
        self.snapshot = snapshot
        self.silent = silent
        self.broken_json = broken_json
        self.mac = mac
        self.subfirmware = subfirmware
        self.http_fail = http_fail
        self.received: list[str] = []
        self.http_received: list[str] = []
        self.logfile_content: str = ""
        self._ws_runner: web.AppRunner | None = None
        self._ws_site: web.TCPSite | None = None
        self._http_runner: web.AppRunner | None = None
        self._http_site: web.TCPSite | None = None
        self.host = "127.0.0.1"
        self.port = 0  # WebSocket port
        self.http_port = 0
        self._connections: set[web.WebSocketResponse] = set()

    @property
    def m2003_text(self) -> str:
        return "M2003" + json.dumps(self.snapshot)

    async def start(self) -> None:
        # WebSocket server (port 8081 in production)
        ws_app = web.Application()
        ws_app.router.add_route("GET", "/", self._handle_ws)
        self._ws_runner = web.AppRunner(ws_app)
        await self._ws_runner.setup()
        self._ws_site = web.TCPSite(self._ws_runner, self.host, 0)
        await self._ws_site.start()
        ws_server = self._ws_site._server
        ws_sock = ws_server.sockets[0]  # type: ignore[union-attr]
        self.port = ws_sock.getsockname()[1]

        # HTTP server (port 8080 in production)
        http_app = web.Application()
        http_app.router.add_route("POST", "/cmd", self._handle_http_cmd)
        http_app.router.add_route("GET", "/cmd", self._handle_http_cmd_get)
        http_app.router.add_route("GET", "/system", self._handle_http_system)
        http_app.router.add_route("POST", "/upload", self._handle_upload)
        http_app.router.add_route(
            "GET", "/gcode/tmp.gcode", self._handle_gcode_download
        )
        http_app.router.add_route("GET", "/gcode/logs.txt", self._handle_logfile)
        http_app.router.add_route("HEAD", "/gcode/logs.txt", self._handle_logfile_head)
        self._http_runner = web.AppRunner(http_app)
        await self._http_runner.setup()
        self._http_site = web.TCPSite(self._http_runner, self.host, 0)
        await self._http_site.start()
        http_server = self._http_site._server
        http_sock = http_server.sockets[0]  # type: ignore[union-attr]
        self.http_port = http_sock.getsockname()[1]

    async def stop(self) -> None:
        for ws in list(self._connections):
            with contextlib.suppress(Exception):
                await ws.close()
        if self._ws_runner is not None:
            await self._ws_runner.cleanup()
        if self._http_runner is not None:
            await self._http_runner.cleanup()
        self._ws_runner = None
        self._ws_site = None
        self._http_runner = None
        self._http_site = None

    # -- HTTP handlers --------------------------------------------------

    async def _handle_http_cmd(self, request: web.Request) -> web.Response:
        if self.http_fail:
            return web.Response(status=500, text="boom")
        body = (await request.read()).decode("ascii", "replace")
        for raw in body.splitlines():
            stripped = raw.strip()
            if stripped:
                self.http_received.append(stripped)
        return web.Response(text='{"result":"ok"}\n')

    async def _handle_http_cmd_get(self, request: web.Request) -> web.Response:
        if self.http_fail:
            return web.Response(status=500, text="boom")
        cmd = request.query.get("cmd", "")
        if cmd:
            self.http_received.append(cmd)
        return web.Response(text=cmd)

    async def _handle_http_system(self, request: web.Request) -> web.Response:
        if self.http_fail:
            return web.Response(status=500, text="boom")
        action = request.query.get("action", "")
        if action == "mac":
            return web.Response(text=self.mac + "\n")
        if action == "version":
            return web.Response(text=self.subfirmware + "\n")
        if action == "get_dev_name":
            return web.Response(text="")
        return web.Response(status=404, text="This URI does not exist")

    async def _handle_upload(self, request: web.Request) -> web.Response:
        if self.http_fail:
            return web.Response(status=500, text="boom")
        await request.read()
        return web.Response(text='{"result":"ok"}\n')

    async def _handle_gcode_download(self, request: web.Request) -> web.Response:
        if self.http_fail:
            return web.Response(status=500, text="boom")
        return web.Response(text="G0X10\nG1X20\n")

    async def _handle_logfile(self, request: web.Request) -> web.Response:
        if self.http_fail:
            return web.Response(status=500, text="boom")
        body = self.logfile_content.encode("utf-8")
        # Support Range header for tail reads
        range_hdr = request.headers.get("Range", "")
        if range_hdr.startswith("bytes=-"):
            tail_n = int(range_hdr[len("bytes=-") :])
            body = body[-tail_n:]
            return web.Response(status=206, body=body)
        return web.Response(body=body)

    async def _handle_logfile_head(self, request: web.Request) -> web.Response:
        if self.http_fail:
            return web.Response(status=500, text="boom")
        size = len(self.logfile_content.encode("utf-8"))
        return web.Response(
            headers={"Content-Length": str(size)},
        )

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

    async def wait_for_received(self, predicate, timeout: float = 1.0) -> str | None:
        """Poll the WS ``received`` list until ``predicate`` matches."""
        return await self._wait_for(self.received, predicate, timeout)

    async def wait_for_http_received(
        self, predicate, timeout: float = 1.0
    ) -> str | None:
        """Poll the HTTP ``http_received`` list until ``predicate`` matches."""
        return await self._wait_for(self.http_received, predicate, timeout)

    @staticmethod
    async def _wait_for(buffer, predicate, timeout: float) -> str | None:
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            for line in buffer:
                if predicate(line):
                    return line
            await asyncio.sleep(0.02)
        return None

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


# --- fake S1 UDP discovery beacon -------------------------------------------


class _FakeUDPDiscoveryProtocol(asyncio.DatagramProtocol):
    """Mimics the xTool S1 UDP discovery beacon for unit tests."""

    def __init__(self, host: str, version: str, name: str) -> None:
        self.host = host
        self.version = version
        self.name = name
        self.transport: asyncio.DatagramTransport | None = None
        self.received: list[bytes] = []

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.received.append(data)
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        request_id = payload.get("requestId")
        reply = json.dumps(
            {
                "requestId": request_id,
                "ip": self.host,
                "name": self.name,
                "version": self.version,
            }
        ).encode("ascii")
        if self.transport is not None:
            self.transport.sendto(reply, addr)


class FakeUDPDiscoveryServer:
    """A loopback UDP server that answers like an S1 discovery beacon."""

    def __init__(
        self,
        *,
        version: str = "V40.32.013.2224.01",
        name: str = "TestLab S1",
    ) -> None:
        self.host = "127.0.0.1"
        self.port = 0
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: _FakeUDPDiscoveryProtocol | None = None
        self._version = version
        self._name = name

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        protocol = _FakeUDPDiscoveryProtocol(self.host, self._version, self._name)
        transport, _ = await loop.create_datagram_endpoint(
            lambda: protocol,
            local_addr=(self.host, 0),
        )
        self._transport = transport
        self._protocol = protocol
        self.port = transport.get_extra_info("sockname")[1]

    async def stop(self) -> None:
        if self._transport is not None:
            self._transport.close()
        self._transport = None
        self._protocol = None


@pytest.fixture
async def fake_udp_discovery_server() -> AsyncIterator[FakeUDPDiscoveryServer]:
    """Yield a started FakeUDPDiscoveryServer on a random loopback port."""
    server = FakeUDPDiscoveryServer()
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
