"""Network client and protocol parser for the xTool S1 laser engraver.

The S1 exposes three network surfaces, all unauthenticated on the local
LAN:

* **TCP 8081 — WebSocket**: pushes state frames in real time. Uses a flat
  M-code dialect (M2003 JSON snapshot, M222/M810/M340/M303/M313/M9039
  push deltas). The xTool Creative Space app can kick this socket while
  it's active — we have to handle that gracefully.
* **TCP 8080 — HTTP REST**: a thin command gateway. ``POST /cmd`` with a
  newline-separated body queues M-codes into the same machine pipeline
  as the WebSocket. The HTTP write path **survives concurrent app
  activity** that kicks the WS — so it's the right place for any write
  operation we expose to HA. ``GET /system?action={mac,version}`` are
  the only working reads.
* **UDP 20000 — JSON discovery**: the S1 replies to a broadcast
  ``{"requestId": <int>}`` with its IP, name and sub-firmware version.
  Used by the config-flow scan to find the device on the LAN.

Critical M-code reference (verified against real hardware
2026-04-08 against hilman2's device):

* ``M2003`` → request full status JSON snapshot, reply on the WS
* ``M303``  → ping / refresh X+Y position, reply on the WS
* ``M222``  → push: current work-state code (``S1``/``S3``/``S10``/...)
* ``M810``  → push: currently loaded job filename
* ``M340``  → push: alarm code (``A0`` = no alarm, anything else = alarm)
* ``M313``  → push: last Z-probe reading
* ``M13``   → fill-light brightness, **read AND write** (``M13 A<n> B<n>``)
* ``M9039`` → AP2 air-cleaner status (deferred to v2)

Note about ``M13``: the original BassXT reverse-engineering work labeled
``A``/``B`` as exhaust fan speeds (a RepRap-style guess). Live testing
showed those are the internal **fill-light brightness** — moving the
brightness slider in XCS changes both fields in lockstep, the audible
exhaust fans are not represented at all in M2003. Real fan-state
discovery is a v2 task. ``M13`` is also a write command — sending
``POST /cmd`` body ``M13 A50 B50`` actually changes the brightness.

Some frames (notably ``M9039``) arrive as binary WebSocket frames with a
non-printable header/footer wrapping the M-code payload — we extract
the printable section.

Original WS protocol details were reverse-engineered in
https://github.com/BassXT/xtool/pull/23. The HTTP and UDP layers were
discovered from scratch in this project.
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

import aiohttp
from aiohttp import (
    ClientError,
    ClientSession,
    ClientWebSocketResponse,
    ClientWSTimeout,
    WSMsgType,
)

from .const import (
    CONFIG_FLOW_PROBE_TIMEOUT,
    HTTP_PORT,
    SCAN_MAX_HOSTS,
    UDP_DISCOVERY_BROADCAST_ADDR,
    UDP_DISCOVERY_PORT,
    UDP_DISCOVERY_TIMEOUT,
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
    firmware_version: str | None = None  # M99
    firmware_aux_1: str | None = None  # M1199
    firmware_aux_2: str | None = None  # M2099
    firmware_tool: str | None = None  # first non-empty entry of M1098 array
    model_name: str | None = None  # M100, e.g. "xTool S1"
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
        http_port: int = HTTP_PORT,
    ) -> None:
        self._host = host
        self._port = port
        self._http_port = http_port
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

    async def set_light_brightness(self, brightness: int) -> None:
        """Set the internal fill-light brightness (0-100).

        Routed through the HTTP ``POST /cmd`` gateway rather than the
        WebSocket so the write survives concurrent XCS-app activity that
        kicks WS connections. Both A and B channels carry the same
        value because the app always sets them in lockstep.

        Confirmed working against a real S1 on 2026-04-08.
        """
        clamped = max(0, min(100, int(brightness)))
        await self.send_command_http(f"M13 A{clamped} B{clamped}")
        # Update state optimistically so the entity reflects the change
        # without waiting for the next push frame.
        self._update_state(
            light_brightness_a=clamped,
            light_brightness_b=clamped,
        )

    # -- HTTP gateway (port 8080) --------------------------------------

    @property
    def http_port(self) -> int:
        """Return the configured HTTP port."""
        return self._http_port

    @property
    def _http_base(self) -> str:
        return f"http://{self._host}:{self._http_port}"

    async def send_command_http(self, gcode: str | list[str]) -> None:
        """Send one or more M-codes via the HTTP ``POST /cmd`` gateway.

        Fire-and-forget — the device acks with ``{"result":"ok"}`` and
        the actual M-code reply (if any) is delivered as a WebSocket
        push frame to all connected clients. We do NOT depend on the
        WS being open here: HTTP writes work even when the XCS app has
        kicked us off the WS.

        Raises:
            XToolS1ConnectionError: if the HTTP request fails.
        """
        if isinstance(gcode, str):
            body_str = gcode if gcode.endswith("\n") else gcode + "\n"
        else:
            body_str = "".join(
                (line if line.endswith("\n") else line + "\n") for line in gcode
            )
        try:
            async with self._session.post(
                f"{self._http_base}/cmd",
                data=body_str.encode("ascii"),
                timeout=aiohttp.ClientTimeout(total=_SEND_TIMEOUT),
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as resp:
                if resp.status != 200:
                    raise XToolS1ConnectionError(
                        f"HTTP /cmd returned status {resp.status}"
                    )
                # Drain the body so the connection can be released.
                await resp.read()
        except (TimeoutError, ClientError, OSError) as err:
            _LOGGER.debug("S1 %s HTTP /cmd failed: %s", self._host, err)
            raise XToolS1ConnectionError(str(err)) from err

    async def fetch_mac_http(self) -> str | None:
        """Read the device MAC via ``GET /system?action=mac``."""
        return await self._fetch_system_action("mac")

    async def fetch_subfirmware_http(self) -> str | None:
        """Read the sub-firmware version via ``GET /system?action=version``.

        This is the same string the WS-side ``M2099`` field carries.
        Useful as a lightweight HTTP heartbeat when the WS is being
        kicked by the app.
        """
        return await self._fetch_system_action("version")

    async def _fetch_system_action(self, action: str) -> str | None:
        try:
            async with self._session.get(
                f"{self._http_base}/system",
                params={"action": action},
                timeout=aiohttp.ClientTimeout(total=_SEND_TIMEOUT),
            ) as resp:
                if resp.status != 200:
                    return None
                text = (await resp.text()).strip()
                return text or None
        except (TimeoutError, ClientError, OSError) as err:
            _LOGGER.debug("S1 %s /system?action=%s failed: %s", self._host, action, err)
            raise XToolS1ConnectionError(str(err)) from err

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

    def _parse_m2003_snapshot(self, json_str: str) -> dict[str, Any]:  # noqa: PLR0912
        """Parse the JSON body of an ``M2003`` reply.

        Each M-code is a separate field that may or may not be present
        in the snapshot — the dispatch is intentionally flat for
        readability, so PLR0912 is silenced.
        """
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

        # Sub-firmwares (M1199 / M2099) and tool firmware (M1098 array).
        # The M1098 field is a 10-slot array; we surface the first
        # non-empty entry as the active tool firmware.
        if (m1199 := data.get("M1199")) is not None:
            out["firmware_aux_1"] = str(m1199).strip() or None
        if (m2099 := data.get("M2099")) is not None:
            out["firmware_aux_2"] = str(m2099).strip() or None
        if isinstance((m1098 := data.get("M1098")), list):
            for slot in m1098:
                if isinstance(slot, str) and slot.strip():
                    out["firmware_tool"] = slot.strip()
                    break

        if (m100 := data.get("M100")) is not None:
            out["model_name"] = str(m100).strip() or None

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
    """A device found by :func:`discover_devices`.

    The fields populated by the UDP discovery beacon (host, name,
    firmware_version) are always present; serial_number is filled in
    later by a WebSocket M2003 probe in the config flow.
    """

    host: str
    name: str | None = None
    firmware_version: str | None = None
    serial_number: str | None = None


class NetworkTooLargeError(XToolS1Error):
    """Raised when a CIDR range passed to :func:`parse_network` is too large."""


def parse_network(value: str) -> ipaddress.IPv4Network:
    """Parse a CIDR string and reject ranges that are too large.

    Used by the config flow to validate the user's broadcast input.

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


# --- UDP discovery ---------------------------------------------------------


def _resolve_broadcast_target(target: str) -> str:
    """Turn a CIDR (or plain IP) into a broadcast address.

    ``192.168.1.0/24``  → ``192.168.1.255``
    ``192.168.1.255``   → ``192.168.1.255``
    ``255.255.255.255`` → ``255.255.255.255``
    """
    try:
        return str(ipaddress.IPv4Network(target, strict=False).broadcast_address)
    except ValueError:
        # Already a plain IP — pass through.
        return target


class _UDPDiscoveryProtocol(asyncio.DatagramProtocol):
    """Collect JSON discovery replies from xTool S1 devices on UDP 20000."""

    def __init__(self, request_id: int) -> None:
        self.request_id = request_id
        self.replies: list[DiscoveredDevice] = []
        self._seen_hosts: set[str] = set()

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        host = addr[0]
        if host in self._seen_hosts:
            return
        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return
        if not isinstance(payload, dict):
            return
        # The S1 echoes the requestId — drop foreign replies just in case.
        if payload.get("requestId") not in (self.request_id, str(self.request_id)):
            return
        self._seen_hosts.add(host)
        name = payload.get("name") or None
        version = payload.get("version") or None
        ip = str(payload.get("ip") or host)
        self.replies.append(
            DiscoveredDevice(
                host=ip,
                name=name,
                firmware_version=version,
            )
        )


async def discover_via_udp(
    broadcast_target: str = UDP_DISCOVERY_BROADCAST_ADDR,
    *,
    port: int = UDP_DISCOVERY_PORT,
    timeout: float = UDP_DISCOVERY_TIMEOUT,
    request_id: int | None = None,
) -> list[DiscoveredDevice]:
    """Broadcast a JSON discovery request and collect S1 replies.

    Sends ``{"requestId": <id>}`` to ``broadcast_target`` on UDP
    ``port`` and listens for ``timeout`` seconds. Every xTool S1 on the
    LAN that receives the broadcast answers with its IP, name and
    sub-firmware version.

    ``broadcast_target`` may be either a plain IP literal (in which
    case the request is sent unicast — useful when you already know
    the host) or a CIDR (in which case its broadcast address is used).
    The ``port`` parameter is overridable to make the function unit
    testable against a fake UDP server on a high port.
    """
    if request_id is None:
        request_id = int.from_bytes(__import__("os").urandom(3), "big")
    target = _resolve_broadcast_target(broadcast_target)
    payload = json.dumps({"requestId": request_id}).encode("ascii")

    loop = asyncio.get_running_loop()
    protocol = _UDPDiscoveryProtocol(request_id)
    transport, _ = await loop.create_datagram_endpoint(
        lambda: protocol,
        local_addr=("0.0.0.0", 0),
        allow_broadcast=True,
    )
    try:
        transport.sendto(payload, (target, port))
        await asyncio.sleep(timeout)
    finally:
        transport.close()
    return list(protocol.replies)


async def discover_devices(
    session: ClientSession,
    broadcast_target: str = UDP_DISCOVERY_BROADCAST_ADDR,
    *,
    port: int = UDP_DISCOVERY_PORT,
    timeout: float = UDP_DISCOVERY_TIMEOUT,
) -> list[DiscoveredDevice]:
    """Find xTool S1 devices on the local network.

    Thin wrapper around :func:`discover_via_udp` — kept as the public
    entry point so callers don't need to know whether the underlying
    transport is UDP, TCP, or anything else. The ``session`` argument
    is unused for the UDP path but kept for backwards compatibility
    with callers that want a future fallback to switch to.
    """
    del session  # reserved for a future HTTP-based fallback
    return await discover_via_udp(broadcast_target, port=port, timeout=timeout)
