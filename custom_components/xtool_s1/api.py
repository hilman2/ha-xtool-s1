"""WebSocket client and protocol parser for the xTool S1 laser engraver.

The S1 firmware exposes a WebSocket on port 8081 that speaks a flat
G-code/M-code dialect. The relevant messages are:

* ``M2003``  → request a full status JSON snapshot
* ``M303``   → ping / refresh X+Y position
* ``M222``   → push: current work-state code (``S1``/``S3``/``S10``/...)
* ``M810``   → push: currently loaded job filename
* ``M340``   → push: alarm code (``A0`` = no alarm, anything else = alarm)
* ``M313``   → push: last Z-probe reading
* ``M9039``  → AP2 air-cleaner status (deferred to v2)

Note about ``M13``: previous reverse-engineering work labeled M13's
``A``/``B`` fields as exhaust fan speeds (RepRap-style). Empirical
testing on a real S1 (smoke test 2026-04-08) showed those fields are
actually the **internal fill-light brightness** — moving the brightness
slider in xTool Creative Space changes both A and B in lockstep, while
the audible exhaust fans are not represented at all. Real fan-state
discovery is a v2 task; we expose only a single "Light Brightness"
reading from this field for now.

The wire format is mostly ASCII, but some frames (notably ``M9039``) arrive
as binary frames with a binary header/footer wrapping the M-code payload.

Protocol details were originally reverse-engineered in
https://github.com/BassXT/xtool/pull/23. This module is a clean
greenfield reimplementation focused on the S1.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import contextlib
from dataclasses import dataclass, field, replace
import ipaddress
import json
import logging
import re
from typing import Any

from aiohttp import (
    ClientError,
    ClientSession,
    ClientWebSocketResponse,
    ClientWSTimeout,
    WSMsgType,
)

from .const import (
    CONFIG_FLOW_PROBE_TIMEOUT,
    SCAN_DEFAULT_CONCURRENCY,
    SCAN_MAX_HOSTS,
    SCAN_TCP_TIMEOUT,
    SCAN_WS_TIMEOUT,
    WS_PORT,
)

_LOGGER = logging.getLogger(__name__)

_CONNECT_TIMEOUT = 8.0
_SEND_TIMEOUT = 5.0
_HEARTBEAT = 30.0

# --- regex patterns ---------------------------------------------------------

# M27 position payload looks like ``X-0.010 Y99.200 Z0.000 U0.000``.
_M27_AXES: dict[str, str] = {"X": "pos_x", "Y": "pos_y", "Z": "pos_z", "U": "pos_u"}

# M105 temperature payload has NO whitespace between axes: ``X0.00Y0.00Z0.00``.
_M105_RE = re.compile(r"([XYZ])([+-]?\d+\.\d+)")
_M105_AXES: dict[str, str] = {"X": "temp_x", "Y": "temp_y", "Z": "temp_z"}

# M313 probe value: ``Zxx.xxx``.
_M313_RE = re.compile(r"Z([+-]?\d+\.\d+)")

# M340 alarm code: ``Axx`` or ``A0``.
_M340_RE = re.compile(r"A(\S+)")

# M810 job file is quoted: ``"filename.gcode"`` (or ``"NULL"`` when idle).
_M810_RE = re.compile(r'"([^"]*)"')

# M303 position payload (push form): ``X-0.010 Y99.200``.
_M303_RE = re.compile(r"X([+-]?\d+\.\d+)\s+Y([+-]?\d+\.\d+)")

# M222 work-state in push form: ``Sxx``.
_M222_RE = re.compile(r"S(\S+)")


# --- exceptions -------------------------------------------------------------


class XToolS1Error(Exception):
    """Base error for the xTool S1 client."""


class XToolS1ConnectionError(XToolS1Error):
    """Raised when the WebSocket cannot be opened or has been lost."""


class XToolS1ProtocolError(XToolS1Error):
    """Raised on malformed protocol frames or unexpected device replies."""


# --- state model ------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class XToolS1State:
    """Immutable snapshot of the S1's reported state.

    A fresh instance is emitted every time the WebSocket listener
    parses a frame, so consumers can safely diff against the previous
    snapshot.
    """

    # Static (cached across frames)
    serial_number: str | None = None
    firmware_version: str | None = None
    tool_type: str | None = None

    # Work state
    work_state_raw: str | None = None

    # Job
    job_file: str | None = None

    # Position / probe (mm)
    pos_x: float | None = None
    pos_y: float | None = None
    pos_z: float | None = None
    pos_u: float | None = None
    probe_z: float | None = None

    # Internal fill-light brightness (percent). Two channels (A and B)
    # but they always carry the same value via the app — see api module
    # docstring. The sensor layer only exposes one of them.
    light_brightness_a: int | None = None
    light_brightness_b: int | None = None

    # Temperatures (°C) — currently only exposed via diagnostics
    temp_x: float | None = None
    temp_y: float | None = None
    temp_z: float | None = None

    # Alarm
    alarm_raw: str | None = None
    alarm_present: bool = False

    # Connection liveness — derived, not from the wire
    connected: bool = False

    # Raw last-update timestamp for diagnostics
    extras: dict[str, Any] = field(default_factory=dict)


# --- parser helpers ---------------------------------------------------------


def _parse_m27_payload(value: str) -> dict[str, float]:
    """Parse an ``M27`` payload like ``X-0.010 Y99.200 Z0.000 U0.000``."""
    out: dict[str, float] = {}
    # ``str.split()`` with no arguments collapses runs of whitespace, so
    # we never see empty parts here.
    for part in value.split():
        axis = part[0]
        if axis not in _M27_AXES:
            continue
        try:
            out[_M27_AXES[axis]] = float(part[1:])
        except ValueError:
            continue
    return out


def _parse_m105_payload(value: str) -> dict[str, float]:
    """Parse a packed ``M105`` payload like ``X0.00Y0.00Z0.00``."""
    out: dict[str, float] = {}
    for match in _M105_RE.finditer(value):
        axis = match.group(1)
        # The regex constrains the axis class to ``[XYZ]`` so the lookup
        # always hits — but keep the membership test for safety.
        if axis not in _M105_AXES:  # pragma: no cover
            continue
        # The numeric group is `[+-]?\d+\.\d+`, so float() can never raise
        # here. We swallow ValueError defensively in case the regex is
        # tightened later.
        try:
            out[_M105_AXES[axis]] = float(match.group(2))
        except ValueError:  # pragma: no cover
            continue
    return out


def _parse_m13_payload(value: str) -> dict[str, int]:
    """Parse an ``M13`` light-brightness payload like ``A70 B70``.

    Despite the RepRap convention of M13 being a fan command, on the
    xTool S1 this field carries the **internal fill-light brightness**
    (0-100). Both ``A`` and ``B`` always carry the same value when set
    via xTool Creative Space — likely two physical LED banks wired to
    one logical setting. We capture both for diagnostics but only one
    sensor is exposed.
    """
    out: dict[str, int] = {}
    # ``str.split()`` with no arguments collapses whitespace, so empty
    # parts never appear here.
    for part in value.split():
        try:
            if part.startswith("A"):
                out["light_brightness_a"] = int(part[1:])
            elif part.startswith("B"):
                out["light_brightness_b"] = int(part[1:])
        except ValueError:
            continue
    return out


def _normalise_alarm(raw: str) -> tuple[str, bool]:
    """Return ``(raw_code, present)`` for an M340 payload."""
    raw = raw.strip()
    present = raw not in ("A0", "0", "")
    return raw, present


# --- client -----------------------------------------------------------------

StateCallback = Callable[[XToolS1State], None]


class XToolS1Client:
    """Async WebSocket client for the xTool S1.

    The client only opens / closes the connection and parses frames.
    It does NOT manage reconnect attempts on its own — the
    :class:`XToolS1Coordinator` calls :meth:`connect` again whenever
    its watchdog poll detects the socket is down.

    Pushed state updates are delivered to subscribers registered via
    :meth:`on_state`.
    """

    def __init__(
        self,
        host: str,
        session: ClientSession,
        *,
        port: int = WS_PORT,
    ) -> None:
        self._host = host
        self._port = port
        self._session = session
        self._ws: ClientWebSocketResponse | None = None
        self._listen_task: asyncio.Task[None] | None = None
        self._state = XToolS1State()
        self._subscribers: list[StateCallback] = []
        self._lock = asyncio.Lock()

    # -- properties -----------------------------------------------------

    @property
    def host(self) -> str:
        """Return the configured device IP / hostname."""
        return self._host

    @property
    def port(self) -> int:
        """Return the configured WebSocket port."""
        return self._port

    @property
    def connected(self) -> bool:
        """Return whether the WebSocket is currently open."""
        return self._ws is not None and not self._ws.closed

    @property
    def state(self) -> XToolS1State:
        """Return the latest immutable state snapshot."""
        return self._state

    # -- subscriptions --------------------------------------------------

    def on_state(self, callback: StateCallback) -> Callable[[], None]:
        """Register a push callback. Returns an unsubscribe function."""
        self._subscribers.append(callback)

        def _unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._subscribers.remove(callback)

        return _unsubscribe

    # -- lifecycle ------------------------------------------------------

    async def connect(self) -> None:
        """Open the WebSocket and start the background listener.

        Raises:
            XToolS1ConnectionError: if the socket cannot be opened.
        """
        async with self._lock:
            if self.connected:
                return
            url = f"ws://{self._host}:{self._port}/"
            try:
                self._ws = await self._session.ws_connect(
                    url,
                    timeout=ClientWSTimeout(ws_close=_CONNECT_TIMEOUT),
                    heartbeat=_HEARTBEAT,
                )
            except (TimeoutError, ClientError, OSError) as err:
                _LOGGER.debug("S1 %s ws_connect failed: %s", self._host, err)
                self._ws = None
                self._update_state(connected=False)
                raise XToolS1ConnectionError(str(err)) from err
            self._update_state(connected=True)
            self._listen_task = asyncio.create_task(
                self._listen_loop(),
                name=f"xtool_s1_listen_{self._host}",
            )
            _LOGGER.debug("S1 %s WebSocket connected", self._host)

    async def disconnect(self) -> None:
        """Cancel the listener and close the WebSocket cleanly."""
        async with self._lock:
            if self._listen_task and not self._listen_task.done():
                self._listen_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await self._listen_task
            self._listen_task = None
            if self._ws is not None and not self._ws.closed:
                await self._ws.close()
            self._ws = None
            self._update_state(connected=False)

    # -- commands -------------------------------------------------------

    async def request_status(self) -> None:
        """Send ``M2003`` to request a full status snapshot."""
        await self._send("M2003\n")

    async def ping(self) -> None:
        """Send ``M303`` as a keepalive / position refresh."""
        await self._send("M303\n")

    async def probe_initial_state(
        self, timeout: float = CONFIG_FLOW_PROBE_TIMEOUT
    ) -> XToolS1State:
        """Connect, request status, and wait for a populated state.

        Used by the config flow as the test-before-configure check.
        Raises XToolS1ConnectionError on connect failure or timeout.
        """
        await self.connect()
        try:
            await self.request_status()
        except XToolS1ConnectionError:
            await self.disconnect()
            raise
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if self._state.serial_number is not None:
                return self._state
            await asyncio.sleep(0.1)
        raise XToolS1ConnectionError(
            f"S1 {self._host} did not return a serial number within {timeout:.1f}s"
        )

    async def _send(self, text: str) -> None:
        if not self.connected:
            raise XToolS1ConnectionError(f"S1 {self._host} not connected")
        assert self._ws is not None  # nosec - guarded by `connected` above
        try:
            await asyncio.wait_for(self._ws.send_str(text), timeout=_SEND_TIMEOUT)
        except (TimeoutError, ClientError, OSError) as err:
            _LOGGER.debug("S1 %s send error: %s", self._host, err)
            raise XToolS1ConnectionError(str(err)) from err

    # -- listener -------------------------------------------------------

    async def _listen_loop(self) -> None:
        """Read frames from the WebSocket until it closes."""
        ws = self._ws
        if ws is None:  # pragma: no cover — only happens on a torn-down race
            return
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    self._handle_frame(msg.data)
                elif msg.type == WSMsgType.BINARY:
                    self._handle_binary_frame(msg.data)
                elif msg.type in (WSMsgType.CLOSE, WSMsgType.ERROR, WSMsgType.CLOSED):
                    break
        except asyncio.CancelledError:
            raise
        except Exception as err:
            _LOGGER.debug("S1 %s listen loop error: %s", self._host, err)
        finally:
            _LOGGER.debug("S1 %s listener stopped", self._host)
            self._ws = None
            self._update_state(connected=False)

    def _handle_binary_frame(self, payload: bytes) -> None:
        """Extract a textual M-code payload from a binary frame, if any.

        Some S1 frames (notably M9039) arrive as binary blobs with
        non-printable header/footer bytes wrapping a printable M-code
        body. We pull out the printable section and feed it back into
        the regular text-frame handler.
        """
        # latin-1 always succeeds (every byte maps to a codepoint), so
        # there's no UnicodeDecodeError path to guard.
        decoded = payload.decode("latin-1")
        # Stop the match at the first control byte so trailing junk
        # doesn't end up inside the captured M-code payload.
        match = re.search(r"(M\d+ [^\x00-\x1f]+)", decoded)
        if match:
            self._handle_frame(match.group(1))

    def _handle_frame(self, text: str) -> None:
        """Parse a single text frame and emit a state update if anything changed."""
        text = text.strip()
        if not text:
            return
        try:
            updates = self._parse_frame(text)
        except (json.JSONDecodeError, ValueError) as err:
            _LOGGER.debug(
                "S1 %s frame parse error: %s | text=%r", self._host, err, text
            )
            return
        if updates:
            self._update_state(**updates)

    def _parse_frame(self, text: str) -> dict[str, Any]:  # noqa: PLR0911, PLR0912
        """Map a single frame to a dict of state updates.

        The dispatch is intentionally flat: each M-code branch is short
        and self-contained, so PLR0911/PLR0912 are silenced for the sake
        of readability.
        """
        # M2003 full snapshot — JSON body glued to the prefix.
        if text.startswith("M2003{"):
            return self._parse_m2003_snapshot(text[len("M2003") :])

        # Push frames are ``M<code> <payload>``.
        if " " not in text:
            return {}
        head, _, tail = text.partition(" ")

        if head == "M222":
            match = _M222_RE.search(tail)
            if not match:
                return {}
            return {"work_state_raw": "S" + match.group(1)}

        if head == "M810":
            match = _M810_RE.search(tail)
            if not match:
                return {}
            value = match.group(1)
            return {"job_file": None if value.upper() == "NULL" else value}

        if head == "M340":
            match = _M340_RE.search(tail)
            if not match:
                return {}
            raw, present = _normalise_alarm(match.group(0))
            return {"alarm_raw": raw, "alarm_present": present}

        if head == "M303":
            match = _M303_RE.search(tail)
            if not match:
                return {}
            # The regex captures numeric `[+-]?\d+\.\d+` groups; the
            # float() conversions cannot raise. The except is defensive.
            try:
                return {"pos_x": float(match.group(1)), "pos_y": float(match.group(2))}
            except ValueError:  # pragma: no cover
                return {}

        if head == "M313":
            match = _M313_RE.search(tail)
            if not match:
                return {}
            try:
                return {"probe_z": float(match.group(1))}
            except ValueError:  # pragma: no cover
                return {}

        # v2: AP2 support — M9039 air-cleaner frames go here.

        return {}

    def _parse_m2003_snapshot(self, json_str: str) -> dict[str, Any]:
        """Parse the JSON body of an ``M2003`` reply."""
        data = json.loads(json_str)
        if not isinstance(data, dict):
            raise XToolS1ProtocolError("M2003 body is not a JSON object")
        out: dict[str, Any] = {}

        if (m222 := data.get("M222")) is not None:
            raw = str(m222).strip()
            if raw and not raw.startswith("S"):
                raw = "S" + raw
            out["work_state_raw"] = raw or None

        if (m27 := data.get("M27")) is not None:
            out.update(_parse_m27_payload(str(m27)))

        if (m310 := data.get("M310")) is not None:
            out["serial_number"] = str(m310).strip() or None

        if (m99 := data.get("M99")) is not None:
            out["firmware_version"] = str(m99).strip() or None

        if (m54 := data.get("M54")) is not None:
            out["tool_type"] = str(m54).strip() or None

        if (m105 := data.get("M105")) is not None:
            out.update(_parse_m105_payload(str(m105)))

        if (m13 := data.get("M13")) is not None:
            out.update(_parse_m13_payload(str(m13)))

        if (m340 := data.get("M340")) is not None:
            raw, present = _normalise_alarm(str(m340))
            out["alarm_raw"] = raw
            out["alarm_present"] = present

        if (m810 := data.get("M810")) is not None:
            value = str(m810).strip().strip('"')
            out["job_file"] = None if value.upper() == "NULL" else value

        return out

    # -- state plumbing -------------------------------------------------

    def _update_state(self, **changes: Any) -> None:
        """Apply ``changes`` and notify subscribers."""
        if not changes:
            return
        self._state = replace(self._state, **changes)
        for callback in list(self._subscribers):
            try:
                callback(self._state)
            except Exception:
                _LOGGER.exception("S1 %s subscriber callback failed", self._host)

    # -- async context manager -----------------------------------------

    async def __aenter__(self) -> XToolS1Client:
        await self.connect()
        return self

    async def __aexit__(self, *exc_info: Any) -> None:
        await self.disconnect()


# --- network discovery ------------------------------------------------------


@dataclass(slots=True, frozen=True)
class DiscoveredDevice:
    """A device found by :func:`discover_devices`."""

    host: str
    serial_number: str
    firmware_version: str | None = None


class NetworkTooLargeError(XToolS1Error):
    """Raised when a network range exceeds :data:`SCAN_MAX_HOSTS`."""


def parse_network(value: str) -> ipaddress.IPv4Network:
    """Parse a CIDR string and reject ranges that are too large.

    Raises:
        ValueError: if ``value`` is not a valid CIDR.
        NetworkTooLargeError: if the network has more than
            :data:`SCAN_MAX_HOSTS` hosts.
    """
    network = ipaddress.IPv4Network(value, strict=False)
    if network.num_addresses > SCAN_MAX_HOSTS:
        raise NetworkTooLargeError(
            f"network {network} has {network.num_addresses} hosts; "
            f"max is {SCAN_MAX_HOSTS}"
        )
    return network


async def _tcp_probe(host: str, port: int, timeout: float) -> bool:
    """Return True if a TCP connection to ``host:port`` succeeds quickly."""
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), timeout=timeout
        )
    except (TimeoutError, OSError):
        return False
    with contextlib.suppress(TimeoutError, OSError):
        writer.close()
        await writer.wait_closed()
    return True


async def _identify_host(
    host: str, port: int, session: ClientSession, ws_timeout: float
) -> DiscoveredDevice | None:
    """Try to talk M2003 to ``host``. Return a :class:`DiscoveredDevice` on success."""
    client = XToolS1Client(host, session, port=port)
    try:
        state = await client.probe_initial_state(timeout=ws_timeout)
    except XToolS1Error:
        return None
    finally:
        await client.disconnect()
    if state.serial_number is None:
        return None
    return DiscoveredDevice(
        host=host,
        serial_number=state.serial_number,
        firmware_version=state.firmware_version,
    )


async def discover_devices(
    session: ClientSession,
    network: ipaddress.IPv4Network | str,
    *,
    port: int = WS_PORT,
    concurrency: int = SCAN_DEFAULT_CONCURRENCY,
    tcp_timeout: float = SCAN_TCP_TIMEOUT,
    ws_timeout: float = SCAN_WS_TIMEOUT,
) -> list[DiscoveredDevice]:
    """Scan ``network`` for xTool S1 devices on ``port``.

    The scan runs a fast parallel TCP probe first; only hosts that
    answer on the target port are then escalated to a WebSocket M2003
    probe to confirm they are actually an S1 (and to read their serial).

    Raises:
        NetworkTooLargeError: if ``network`` exceeds :data:`SCAN_MAX_HOSTS`.
    """
    if isinstance(network, str):
        network = parse_network(network)
    elif network.num_addresses > SCAN_MAX_HOSTS:
        raise NetworkTooLargeError(
            f"network {network} has {network.num_addresses} hosts; "
            f"max is {SCAN_MAX_HOSTS}"
        )

    semaphore = asyncio.Semaphore(concurrency)
    candidates: list[str] = []

    async def _stage1(host: str) -> None:
        async with semaphore:
            if await _tcp_probe(host, port, tcp_timeout):
                candidates.append(host)

    await asyncio.gather(*(_stage1(str(addr)) for addr in network.hosts()))

    # Stage 2: confirm with WebSocket M2003 (sequentially — typically only
    # 0-2 candidates per home net, no need for parallelism here).
    found: list[DiscoveredDevice] = []
    for host in candidates:
        device = await _identify_host(host, port, session, ws_timeout)
        if device is not None:
            found.append(device)
    return found
