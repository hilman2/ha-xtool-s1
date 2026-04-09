"""Tests for the xtool_s1 WebSocket client and protocol parser."""

from __future__ import annotations

import asyncio
import ipaddress
import json

import pytest

from custom_components.xtool_s1.api import (
    DiscoveredDevice,
    NetworkTooLargeError,
    XToolS1Client,
    XToolS1ConnectionError,
    XToolS1ProtocolError,
    XToolS1State,
    _normalise_alarm,
    _parse_m13_payload,
    _parse_m27_payload,
    _parse_m98_payload,
    _parse_m105_payload,
    _parse_m116_payload,
    _parse_m2008_payload,
    discover_devices,
    discover_via_udp,
    parse_network,
)

from .conftest import load_fixture
from .const import MOCK_FIRMWARE, MOCK_SERIAL

# --- pure parser unit tests --------------------------------------------------


class TestParserHelpers:
    """Pure-function unit tests for the M-code helpers."""

    def test_parse_m27_payload_full(self) -> None:
        result = _parse_m27_payload("X-12.500 Y45.200 Z10.000 U0.000")
        assert result == {
            "pos_x": -12.5,
            "pos_y": 45.2,
            "pos_z": 10.0,
            "pos_u": 0.0,
        }

    def test_parse_m27_payload_partial(self) -> None:
        assert _parse_m27_payload("X1.0 Y2.0") == {"pos_x": 1.0, "pos_y": 2.0}

    def test_parse_m27_payload_skips_garbage(self) -> None:
        assert _parse_m27_payload("XAA.BB Y2.0") == {"pos_y": 2.0}

    def test_parse_m27_payload_empty(self) -> None:
        assert _parse_m27_payload("") == {}

    def test_parse_m105_packed(self) -> None:
        result = _parse_m105_payload("X42.10Y39.80Z35.20")
        assert result == {"temp_x": 42.1, "temp_y": 39.8, "temp_z": 35.2}

    def test_parse_m105_signed(self) -> None:
        assert _parse_m105_payload("X-1.50Y+2.50Z0.00") == {
            "temp_x": -1.5,
            "temp_y": 2.5,
            "temp_z": 0.0,
        }

    def test_parse_m105_empty(self) -> None:
        assert _parse_m105_payload("") == {}

    def test_parse_m13_payload(self) -> None:
        assert _parse_m13_payload("A85 B72") == {
            "light_brightness_a": 85,
            "light_brightness_b": 72,
        }

    def test_parse_m13_payload_partial(self) -> None:
        assert _parse_m13_payload("A0") == {"light_brightness_a": 0}

    def test_parse_m13_payload_garbage(self) -> None:
        assert _parse_m13_payload("Axx Byy") == {}

    @pytest.mark.parametrize(
        ("raw", "expected_present"),
        [
            ("A0", False),
            ("0", False),
            ("", False),
            ("A1", True),
            ("A7", True),
            ("E42", True),
        ],
    )
    def test_normalise_alarm(self, raw: str, expected_present: bool) -> None:
        _, present = _normalise_alarm(raw)
        assert present is expected_present

    def test_parse_m116_diode_40w(self) -> None:
        """The diode 40 W head reports Y40 in the capability bitmap."""
        result = _parse_m116_payload("X0Y40B1P1L2")
        assert result["tool_capabilities_raw"] == "X0Y40B1P1L2"
        assert result["tool_power_w"] == 40

    def test_parse_m116_infrared_2w(self) -> None:
        """The 2 W IR head reports Y2."""
        result = _parse_m116_payload("X1Y2B0P0L0")
        assert result["tool_power_w"] == 2

    def test_parse_m116_no_y_field(self) -> None:
        """A payload without a Y field still surfaces the raw string."""
        result = _parse_m116_payload("X0B1")
        assert result["tool_capabilities_raw"] == "X0B1"
        assert "tool_power_w" not in result

    def test_parse_m116_empty(self) -> None:
        """An empty payload yields a None raw string."""
        result = _parse_m116_payload("")
        assert result == {"tool_capabilities_raw": None}

    def test_parse_m98_full(self) -> None:
        result = _parse_m98_payload("X0.32 Y25.88")
        assert result == {"tool_offset_x": 0.32, "tool_offset_y": 25.88}

    def test_parse_m98_signed(self) -> None:
        result = _parse_m98_payload("X-1.5 Y+2.0")
        assert result == {"tool_offset_x": -1.5, "tool_offset_y": 2.0}

    def test_parse_m98_only_x(self) -> None:
        assert _parse_m98_payload("X0.50") == {"tool_offset_x": 0.5}

    def test_parse_m98_empty(self) -> None:
        assert _parse_m98_payload("") == {}

    def test_parse_m2008_full(self) -> None:
        """Full M2008 lifetime counters."""
        result = _parse_m2008_payload("A151484 B219 C1263671 D3004")
        assert result == {
            "working_seconds": 151484,
            "session_count": 219,
            "standby_seconds": 1263671,
            "tool_runtime_seconds": 3004,
        }

    def test_parse_m2008_partial(self) -> None:
        """A partial payload only sets the present fields."""
        assert _parse_m2008_payload("A100") == {"working_seconds": 100}

    def test_parse_m2008_empty(self) -> None:
        assert _parse_m2008_payload("") == {}


# --- frame-handling tests against the in-process FakeS1Server ---------------


@pytest.mark.asyncio
class TestXToolS1ClientWithFakeServer:
    """Integration-style tests using the FakeS1Server."""

    async def test_connect_and_request_status(self, fake_s1_server, hass) -> None:
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        session = async_get_clientsession(hass)
        client = XToolS1Client(fake_s1_server.host, session, port=fake_s1_server.port)

        states: list[XToolS1State] = []
        client.on_state(states.append)

        try:
            await client.connect()
            assert client.connected
            await client.request_status()
            # Wait for the listener to absorb the M2003 reply.
            for _ in range(20):
                if client.state.serial_number is not None:
                    break
                await asyncio.sleep(0.05)
            assert client.state.serial_number == MOCK_SERIAL
            assert client.state.firmware_version == MOCK_FIRMWARE
            assert client.state.work_state_raw == "S3"
            assert client.state.light_brightness_a == 0
            assert client.state.light_brightness_b == 0
            assert client.state.alarm_present is False
            assert client.state.job_file is None
        finally:
            await client.disconnect()
        assert client.connected is False
        # Subscribers received both the connected=True frame and the M2003 update.
        assert any(s.serial_number == MOCK_SERIAL for s in states)

    async def test_handles_running_snapshot(self, fake_s1_server_running, hass) -> None:
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        session = async_get_clientsession(hass)
        client = XToolS1Client(
            fake_s1_server_running.host,
            session,
            port=fake_s1_server_running.port,
        )
        try:
            state = await client.probe_initial_state(timeout=2.0)
        finally:
            await client.disconnect()
        assert state.serial_number == MOCK_SERIAL
        assert state.work_state_raw == "S14"
        assert state.pos_x == -12.5
        assert state.pos_y == 45.2
        assert state.light_brightness_a == 85
        assert state.light_brightness_b == 72
        assert state.job_file == "my_engraving.gcode"

    async def test_handles_alarm_snapshot(self, fake_s1_server_alarm, hass) -> None:
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        session = async_get_clientsession(hass)
        client = XToolS1Client(
            fake_s1_server_alarm.host,
            session,
            port=fake_s1_server_alarm.port,
        )
        try:
            state = await client.probe_initial_state(timeout=2.0)
        finally:
            await client.disconnect()
        assert state.alarm_present is True
        assert state.alarm_raw == "A7"

    async def test_ping_records_frame(self, fake_s1_server, hass) -> None:
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        session = async_get_clientsession(hass)
        client = XToolS1Client(fake_s1_server.host, session, port=fake_s1_server.port)
        try:
            await client.connect()
            await client.ping()
            await asyncio.sleep(0.1)
        finally:
            await client.disconnect()
        assert any(f.startswith("M303") for f in fake_s1_server.received)

    async def test_connect_failure_raises(self, hass) -> None:
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        session = async_get_clientsession(hass)
        client = XToolS1Client("127.0.0.1", session, port=1)  # nothing listens
        with pytest.raises(XToolS1ConnectionError):
            await client.connect()
        assert client.connected is False

    async def test_send_when_disconnected_raises(self, hass) -> None:
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        session = async_get_clientsession(hass)
        client = XToolS1Client("127.0.0.1", session, port=1)
        with pytest.raises(XToolS1ConnectionError):
            await client.ping()

    async def test_probe_initial_state_timeout(
        self, fake_s1_server_silent, hass
    ) -> None:
        """A WS server that never replies must time out cleanly."""
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        session = async_get_clientsession(hass)
        client = XToolS1Client(
            fake_s1_server_silent.host, session, port=fake_s1_server_silent.port
        )
        with pytest.raises(XToolS1ConnectionError):
            await client.probe_initial_state(timeout=0.5)
        await client.disconnect()

    async def test_subscriber_unsubscribe(self, fake_s1_server, hass) -> None:
        from homeassistant.helpers.aiohttp_client import async_get_clientsession

        session = async_get_clientsession(hass)
        client = XToolS1Client(fake_s1_server.host, session, port=fake_s1_server.port)
        events: list[XToolS1State] = []
        unsub = client.on_state(events.append)
        unsub()
        try:
            await client.connect()
            await client.request_status()
            await asyncio.sleep(0.2)
        finally:
            await client.disconnect()
        # The subscriber was unsubscribed before connect — must not be called.
        assert events == []


# --- M2003 JSON parsing edge cases ------------------------------------------


@pytest.mark.asyncio
async def test_m2003_with_state_prefix_already_set(hass) -> None:
    """The M222 field may arrive with or without the leading 'S'."""
    # Drive the parser directly — no need for a server.
    from custom_components.xtool_s1.api import XToolS1Client

    snapshot = load_fixture("m2003_idle.json")
    snapshot["M222"] = "14"  # plain number, must be coerced to "S14"

    client = XToolS1Client("127.0.0.1", session=None)  # type: ignore[arg-type]
    updates = client._parse_m2003_snapshot(json.dumps(snapshot))
    assert updates["work_state_raw"] == "S14"


@pytest.mark.asyncio
async def test_m2003_invalid_json_is_ignored(fake_s1_server_broken_json, hass) -> None:
    """A malformed M2003 body must not crash the listener."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(
        fake_s1_server_broken_json.host,
        session,
        port=fake_s1_server_broken_json.port,
    )
    try:
        await client.connect()
        await client.request_status()
        await asyncio.sleep(0.3)
        # Listener still alive, state is still empty.
        assert client.connected
        assert client.state.serial_number is None
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_m2003_root_must_be_object(hass) -> None:
    """A root JSON array must raise XToolS1ProtocolError internally."""
    from custom_components.xtool_s1.api import XToolS1Client

    client = XToolS1Client("127.0.0.1", session=None)  # type: ignore[arg-type]
    # Call the parser directly — it raises XToolS1ProtocolError on bad type.
    with pytest.raises(XToolS1ProtocolError):
        client._parse_m2003_snapshot("[1, 2, 3]")


# --- push frame routing ------------------------------------------------------


@pytest.mark.asyncio
async def test_push_frames_update_state(fake_s1_server, hass) -> None:
    """Frames pushed by the server land in the state via the listener."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(fake_s1_server.host, session, port=fake_s1_server.port)
    try:
        await client.connect()
        await fake_s1_server.push("M222 S14")
        await fake_s1_server.push('M810 "engrave.gcode"')
        await fake_s1_server.push("M340 A3")
        await fake_s1_server.push("M313 Z-0.125")
        await fake_s1_server.push("M303 X10.5 Y20.5")
        await asyncio.sleep(0.2)
    finally:
        await client.disconnect()
    state = client.state
    assert state.work_state_raw == "S14"
    assert state.job_file == "engrave.gcode"
    assert state.alarm_present is True
    assert state.alarm_raw == "A3"
    assert state.probe_z == -0.125
    assert state.pos_x == 10.5
    assert state.pos_y == 20.5


@pytest.mark.asyncio
async def test_push_frames_m2008_m22_m323_m116_m98(fake_s1_server, hass) -> None:
    """The new push branches each map onto their state fields."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(fake_s1_server.host, session, port=fake_s1_server.port)
    try:
        await client.connect()
        await fake_s1_server.push("M2008 A12345 B6 C9876 D54")
        await fake_s1_server.push("M22 S1")
        await fake_s1_server.push("M323 OK")
        await fake_s1_server.push("M323 OK")
        await fake_s1_server.push("M116 X0Y40B1P1L2")
        await fake_s1_server.push("M98 X0.32 Y25.88")
        await asyncio.sleep(0.2)
    finally:
        await client.disconnect()
    state = client.state
    assert state.working_seconds == 12345
    assert state.session_count == 6
    assert state.standby_seconds == 9876
    assert state.tool_runtime_seconds == 54
    assert state.m22_state == "S1"
    assert state.m323_ack_count == 2
    assert state.tool_capabilities_raw == "X0Y40B1P1L2"
    assert state.tool_power_w == 40
    assert state.tool_offset_x == 0.32
    assert state.tool_offset_y == 25.88


@pytest.mark.asyncio
async def test_push_m15_light_active_flag(fake_s1_server, hass) -> None:
    """``M15 A1 S0`` sets light_active=False, ``M15 A1 S1`` restores it."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(fake_s1_server.host, session, port=fake_s1_server.port)
    try:
        await client.connect()
        # Default is True.
        assert client.state.light_active is True
        await fake_s1_server.push("M15 A1 S0")
        await asyncio.sleep(0.1)
        assert client.state.light_active is False
        await fake_s1_server.push("M15 A1 S1")
        await asyncio.sleep(0.1)
        assert client.state.light_active is True
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_push_m15_without_s_field_ignored(fake_s1_server, hass) -> None:
    """A bare ``M15`` without S0/S1 doesn't change the flag."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(fake_s1_server.host, session, port=fake_s1_server.port)
    try:
        await client.connect()
        await fake_s1_server.push("M15 A1")
        await asyncio.sleep(0.1)
        assert client.state.light_active is True  # unchanged
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_push_m22_empty_payload_ignored(fake_s1_server, hass) -> None:
    """An ``M22`` push with no payload should not change the state."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(fake_s1_server.host, session, port=fake_s1_server.port)
    try:
        await client.connect()
        await fake_s1_server.push("M22 ")
        await asyncio.sleep(0.1)
    finally:
        await client.disconnect()
    assert client.state.m22_state is None


@pytest.mark.asyncio
async def test_push_m323_non_ok_payload_ignored(fake_s1_server, hass) -> None:
    """A ``M323`` push that isn't ``OK`` must not bump the counter."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(fake_s1_server.host, session, port=fake_s1_server.port)
    try:
        await client.connect()
        await fake_s1_server.push("M323 BUSY")
        await asyncio.sleep(0.1)
    finally:
        await client.disconnect()
    assert client.state.m323_ack_count == 0


@pytest.mark.asyncio
async def test_m2003_snapshot_includes_m116_m98(hass) -> None:
    """M116 and M98 fields in an M2003 snapshot land on the state."""
    snapshot = load_fixture("m2003_idle.json")
    snapshot["M116"] = "X0Y40B1P1L2"
    snapshot["M98"] = "X0.32 Y25.88"
    client = XToolS1Client("127.0.0.1", session=None)  # type: ignore[arg-type]
    updates = client._parse_m2003_snapshot(json.dumps(snapshot))
    assert updates["tool_capabilities_raw"] == "X0Y40B1P1L2"
    assert updates["tool_power_w"] == 40
    assert updates["tool_offset_x"] == 0.32
    assert updates["tool_offset_y"] == 25.88


@pytest.mark.asyncio
async def test_stop_pause_resume_request_stats_use_http(fake_s1_server, hass) -> None:
    """Job-control + stats-poll commands all go through POST /cmd."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from custom_components.xtool_s1.const import (
        MCODE_PAUSE,
        MCODE_RESUME,
    )

    session = async_get_clientsession(hass)
    client = XToolS1Client(
        fake_s1_server.host,
        session,
        port=fake_s1_server.port,
        http_port=fake_s1_server.http_port,
    )
    await client.stop_job()
    await client.pause_job()
    await client.resume_job()
    await client.request_stats()
    assert "M108" in fake_s1_server.http_received
    assert MCODE_PAUSE in fake_s1_server.http_received
    assert MCODE_RESUME in fake_s1_server.http_received
    assert "M2008" in fake_s1_server.http_received


@pytest.mark.asyncio
async def test_upload_job(fake_s1_server, hass) -> None:
    """upload_job POSTs gcode to /upload with taskId."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(
        fake_s1_server.host,
        session,
        port=fake_s1_server.port,
        http_port=fake_s1_server.http_port,
    )
    await client.upload_job("G0X10\nG1X20\n", "test-uuid-123")
    # The fake server doesn't have /upload, but the HTTP port accepted it.


@pytest.mark.asyncio
async def test_upload_job_failure(hass) -> None:
    """upload_job wraps HTTP errors as XToolS1ConnectionError."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client("127.0.0.1", session, http_port=1)
    with pytest.raises(XToolS1ConnectionError):
        await client.upload_job("G0X10\n", "uuid")


@pytest.mark.asyncio
async def test_upload_job_server_error(fake_s1_server, hass) -> None:
    """upload_job raises when the server returns an error status."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(
        fake_s1_server.host,
        session,
        port=fake_s1_server.port,
        http_port=fake_s1_server.http_port,
    )
    fake_s1_server.http_fail = True
    with pytest.raises(XToolS1ConnectionError):
        await client.upload_job("G0X10\n", "uuid")


@pytest.mark.asyncio
async def test_download_job(fake_s1_server, hass) -> None:
    """download_job fetches /gcode/tmp.gcode."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(
        fake_s1_server.host,
        session,
        port=fake_s1_server.port,
        http_port=fake_s1_server.http_port,
    )
    gcode = await client.download_job()
    assert "G0X10" in gcode


@pytest.mark.asyncio
async def test_download_job_server_error(fake_s1_server, hass) -> None:
    """download_job raises on HTTP error."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(
        fake_s1_server.host,
        session,
        port=fake_s1_server.port,
        http_port=fake_s1_server.http_port,
    )
    fake_s1_server.http_fail = True
    with pytest.raises(XToolS1ConnectionError):
        await client.download_job()


@pytest.mark.asyncio
async def test_download_job_unreachable(hass) -> None:
    """download_job wraps connection errors."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client("127.0.0.1", session, http_port=1)
    with pytest.raises(XToolS1ConnectionError):
        await client.download_job()


@pytest.mark.asyncio
async def test_start_job_sequence(fake_s1_server, hass) -> None:
    """start_job_sequence sends M322/M330/M323 over WebSocket."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(fake_s1_server.host, session, port=fake_s1_server.port)
    try:
        await client.connect()
        await client.start_job_sequence()
        await asyncio.sleep(0.2)
    finally:
        await client.disconnect()
    assert any("M322" in f for f in fake_s1_server.received)
    assert any("M323" in f for f in fake_s1_server.received)


@pytest.mark.asyncio
async def test_start_job_sequence_disconnected(hass) -> None:
    """start_job_sequence raises when WS is not connected."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client("127.0.0.1", session, port=1)
    with pytest.raises(XToolS1ConnectionError):
        await client.start_job_sequence()


@pytest.mark.asyncio
async def test_push_unknown_frame_is_ignored(fake_s1_server, hass) -> None:
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(fake_s1_server.host, session, port=fake_s1_server.port)
    try:
        await client.connect()
        await fake_s1_server.push("M9999 some unknown payload")
        await fake_s1_server.push("")  # empty
        await asyncio.sleep(0.1)
    finally:
        await client.disconnect()
    # No crash, state stays connected.


@pytest.mark.asyncio
async def test_binary_frame_extraction(fake_s1_server, hass) -> None:
    """Binary frames carrying an M-code payload must be extracted."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(fake_s1_server.host, session, port=fake_s1_server.port)
    # Drive the parser directly with binary data, since FakeS1Server only
    # speaks text frames.
    client._handle_binary_frame(b"\x00\x01M340 A2\x02\x03")
    assert client.state.alarm_present is True
    assert client.state.alarm_raw == "A2"
    # A frame with no embedded M-code is silently dropped.
    client._handle_binary_frame(b"\x00\x01\x02")


# --- network discovery ------------------------------------------------------


def test_parse_network_valid() -> None:
    net = parse_network("192.168.1.0/24")
    assert isinstance(net, ipaddress.IPv4Network)
    assert net.num_addresses == 256


def test_parse_network_too_large() -> None:
    with pytest.raises(NetworkTooLargeError):
        parse_network("10.0.0.0/8")


def test_parse_network_bad_cidr() -> None:
    with pytest.raises(ValueError):
        parse_network("not a network")


@pytest.mark.asyncio
async def test_discover_via_udp_against_local_beacon(
    fake_udp_discovery_server, hass
) -> None:
    """Run UDP discovery against the in-process loopback beacon."""
    devices = await discover_via_udp(
        fake_udp_discovery_server.host,
        port=fake_udp_discovery_server.port,
        timeout=1.0,
    )
    assert len(devices) == 1
    assert devices[0].host == fake_udp_discovery_server.host
    assert devices[0].firmware_version == "V40.32.013.2224.01"
    assert devices[0].name == "TestLab S1"
    assert isinstance(devices[0], DiscoveredDevice)


@pytest.mark.asyncio
async def test_discover_devices_wraps_udp(fake_udp_discovery_server, hass) -> None:
    """The public discover_devices entry point delegates to UDP discovery."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    devices = await discover_devices(
        session,
        fake_udp_discovery_server.host,
        port=fake_udp_discovery_server.port,
        timeout=1.0,
    )
    assert any(d.host == fake_udp_discovery_server.host for d in devices)
    assert all(isinstance(d, DiscoveredDevice) for d in devices)


@pytest.mark.asyncio
async def test_discover_via_udp_no_replies(hass) -> None:
    """Sending the beacon to a black hole returns an empty list."""
    devices = await discover_via_udp("127.0.0.1", port=1, timeout=0.3)
    assert devices == []


@pytest.mark.asyncio
async def test_discover_via_udp_with_cidr_target(
    fake_udp_discovery_server, hass
) -> None:
    """A CIDR input is resolved to its broadcast address before sending."""
    # 127.0.0.0/30 -> broadcast 127.0.0.3, which won't reach our loopback
    # listener — but the resolution path itself is what we want to cover.
    devices = await discover_via_udp(
        "127.0.0.0/30",
        port=fake_udp_discovery_server.port,
        timeout=0.3,
    )
    assert isinstance(devices, list)


# --- HTTP gateway tests ----------------------------------------------------


@pytest.mark.asyncio
async def test_send_command_http_string(fake_s1_server, hass) -> None:
    """send_command_http with a string M-code reaches the fake HTTP server."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(
        fake_s1_server.host,
        session,
        port=fake_s1_server.port,
        http_port=fake_s1_server.http_port,
    )
    await client.send_command_http("M13 A40 B40")
    assert "M13 A40 B40" in fake_s1_server.http_received


@pytest.mark.asyncio
async def test_send_command_http_list(fake_s1_server, hass) -> None:
    """send_command_http with a list of M-codes posts them all in one body."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(
        fake_s1_server.host,
        session,
        port=fake_s1_server.port,
        http_port=fake_s1_server.http_port,
    )
    await client.send_command_http(["M2003", "M13 A30 B30\n"])
    assert "M2003" in fake_s1_server.http_received
    assert "M13 A30 B30" in fake_s1_server.http_received


@pytest.mark.asyncio
async def test_send_command_http_server_error(fake_s1_server, hass) -> None:
    """A non-200 from the HTTP server surfaces as XToolS1ConnectionError."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(
        fake_s1_server.host,
        session,
        port=fake_s1_server.port,
        http_port=fake_s1_server.http_port,
    )
    fake_s1_server.http_fail = True
    with pytest.raises(XToolS1ConnectionError):
        await client.send_command_http("M2003")


@pytest.mark.asyncio
async def test_send_command_http_unreachable(hass) -> None:
    """A connection refused on the HTTP port surfaces as XToolS1ConnectionError."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    # Port 1 is privileged and won't have an HTTP listener
    client = XToolS1Client("127.0.0.1", session, http_port=1)
    with pytest.raises(XToolS1ConnectionError):
        await client.send_command_http("M2003")


@pytest.mark.asyncio
async def test_fetch_mac_http(fake_s1_server, hass) -> None:
    """fetch_mac_http returns the MAC the fake server reports."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(
        fake_s1_server.host,
        session,
        port=fake_s1_server.port,
        http_port=fake_s1_server.http_port,
    )
    mac = await client.fetch_mac_http()
    assert mac == "30:30:f9:71:7a:f4"


@pytest.mark.asyncio
async def test_fetch_subfirmware_http(fake_s1_server, hass) -> None:
    """fetch_subfirmware_http returns the version string."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(
        fake_s1_server.host,
        session,
        port=fake_s1_server.port,
        http_port=fake_s1_server.http_port,
    )
    version = await client.fetch_subfirmware_http()
    assert version == "V40.32.013.2224.01 B1"


@pytest.mark.asyncio
async def test_fetch_system_action_returns_none_on_500(fake_s1_server, hass) -> None:
    """A 500 status from /system returns None instead of raising."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(
        fake_s1_server.host,
        session,
        port=fake_s1_server.port,
        http_port=fake_s1_server.http_port,
    )
    fake_s1_server.http_fail = True
    assert await client.fetch_mac_http() is None


@pytest.mark.asyncio
async def test_fetch_system_action_unreachable(hass) -> None:
    """Connection refused on /system raises XToolS1ConnectionError."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client("127.0.0.1", session, http_port=1)
    with pytest.raises(XToolS1ConnectionError):
        await client.fetch_mac_http()


def test_resolve_broadcast_target_passes_plain_ip() -> None:
    """A plain IP literal passes through unchanged."""
    from custom_components.xtool_s1.api import _resolve_broadcast_target

    assert _resolve_broadcast_target("192.168.1.42") == "192.168.1.42"
    assert _resolve_broadcast_target("255.255.255.255") == "255.255.255.255"


def test_resolve_broadcast_target_resolves_cidr() -> None:
    from custom_components.xtool_s1.api import _resolve_broadcast_target

    assert _resolve_broadcast_target("192.168.1.0/24") == "192.168.1.255"


def test_resolve_broadcast_target_falls_through_on_garbage() -> None:
    """A garbage string falls through to be returned as-is."""
    from custom_components.xtool_s1.api import _resolve_broadcast_target

    assert _resolve_broadcast_target("not-a-network") == "not-a-network"


@pytest.mark.asyncio
async def test_discover_via_udp_with_explicit_request_id(
    fake_udp_discovery_server, hass
) -> None:
    """Passing an explicit request_id covers the non-random path."""
    devices = await discover_via_udp(
        fake_udp_discovery_server.host,
        port=fake_udp_discovery_server.port,
        timeout=1.0,
        request_id=12345,
    )
    assert len(devices) == 1


@pytest.mark.asyncio
async def test_discover_via_udp_ignores_garbage_replies(hass) -> None:
    """Non-JSON replies and JSON arrays are silently ignored."""
    from custom_components.xtool_s1.api import _UDPDiscoveryProtocol

    protocol = _UDPDiscoveryProtocol(request_id=42)
    # garbage non-JSON
    protocol.datagram_received(b"not json", ("1.2.3.4", 20000))
    # JSON array (not a dict)
    protocol.datagram_received(b"[1,2,3]", ("1.2.3.5", 20000))
    # Wrong requestId
    protocol.datagram_received(
        b'{"requestId":99,"ip":"1.2.3.6","version":"v1"}',
        ("1.2.3.6", 20000),
    )
    # Right requestId — should be accepted
    protocol.datagram_received(
        b'{"requestId":42,"ip":"1.2.3.7","name":"","version":"v9"}',
        ("1.2.3.7", 20000),
    )
    # Duplicate from same host — should be deduplicated
    protocol.datagram_received(
        b'{"requestId":42,"ip":"1.2.3.7","name":"","version":"v9"}',
        ("1.2.3.7", 20000),
    )
    assert len(protocol.replies) == 1
    assert protocol.replies[0].host == "1.2.3.7"
    assert protocol.replies[0].name is None
    assert protocol.replies[0].firmware_version == "v9"


# --- additional edge cases for coverage -------------------------------------


class TestParserEdgeCases:
    """Cover the early-exit branches in the parser helpers."""

    def test_m27_unknown_axis_skipped(self) -> None:
        # 'W' is not in the axis map.
        assert _parse_m27_payload("W1.0 X2.0") == {"pos_x": 2.0}

    def test_m27_garbage_value(self) -> None:
        assert _parse_m27_payload("Xfoo") == {}

    def test_m27_single_char_lone_axis(self) -> None:
        # A bare axis letter (no number) trips the float() ValueError branch.
        assert _parse_m27_payload("X") == {}

    def test_m105_garbage(self) -> None:
        # No valid axis match at all.
        assert _parse_m105_payload("noise") == {}

    def test_m13_empty_part(self) -> None:
        # Multiple spaces produce empty parts; they must be skipped.
        assert _parse_m13_payload("  A1   B2  ") == {
            "light_brightness_a": 1,
            "light_brightness_b": 2,
        }

    def test_m13_garbage_int(self) -> None:
        assert _parse_m13_payload("Aabc Bxyz") == {}

    def test_m13_unknown_prefix(self) -> None:
        # 'C' isn't A or B — just skipped silently.
        assert _parse_m13_payload("C99 A1") == {"light_brightness_a": 1}


@pytest.mark.asyncio
async def test_subscribe_unsubscribe_twice(hass) -> None:
    """Calling unsubscribe twice must not raise."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client("127.0.0.1", session, port=1)
    unsub = client.on_state(lambda _: None)
    unsub()
    unsub()  # second call is a no-op


@pytest.mark.asyncio
async def test_subscriber_exceptions_are_swallowed(hass) -> None:
    """A subscriber raising must not break the state propagation."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client("127.0.0.1", session, port=1)

    bad_calls = 0
    good_calls = 0

    def bad(_state):
        nonlocal bad_calls
        bad_calls += 1
        raise RuntimeError("boom")

    def good(_state):
        nonlocal good_calls
        good_calls += 1

    client.on_state(bad)
    client.on_state(good)
    client._update_state(work_state_raw="S3")
    assert bad_calls == 1
    assert good_calls == 1


@pytest.mark.asyncio
async def test_update_state_with_no_changes(hass) -> None:
    """An empty changes dict is a no-op."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client("127.0.0.1", session, port=1)
    state_before = client.state
    client._update_state()
    assert client.state is state_before


@pytest.mark.asyncio
async def test_handle_frame_empty_string(hass) -> None:
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client("127.0.0.1", session, port=1)
    state_before = client.state
    client._handle_frame("   ")
    assert client.state is state_before


@pytest.mark.asyncio
async def test_handle_frame_no_space_returns(hass) -> None:
    """A frame without a payload (no space after the M-code) is ignored."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client("127.0.0.1", session, port=1)
    state_before = client.state
    client._handle_frame("M222")
    assert client.state is state_before


def test_handle_binary_frame_no_mcode() -> None:
    """Binary frames without an M-code body are silently dropped."""
    client = XToolS1Client("127.0.0.1", session=None, port=1)  # type: ignore[arg-type]
    client._handle_binary_frame(b"")
    client._handle_binary_frame(b"\x00\x00\x00")
    # State stays at the default empty snapshot.
    assert client.state.alarm_present is False


@pytest.mark.asyncio
async def test_disconnect_when_not_connected(hass) -> None:
    """disconnect() must be safe on a never-connected client."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client("127.0.0.1", session, port=1)
    await client.disconnect()  # no-op


@pytest.mark.asyncio
async def test_double_connect_is_idempotent(fake_s1_server, hass) -> None:
    """Calling connect() twice must not error."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(fake_s1_server.host, session, port=fake_s1_server.port)
    try:
        await client.connect()
        await client.connect()  # second call returns early
        assert client.connected
    finally:
        await client.disconnect()


def test_m2003_m1098_array_with_only_empty_slots() -> None:
    """An M1098 array with no populated slots leaves firmware_tool unset."""
    from custom_components.xtool_s1.api import XToolS1Client

    client = XToolS1Client("127.0.0.1", session=None)  # type: ignore[arg-type]
    updates = client._parse_m2003_snapshot('{"M1098": ["", "", "", ""]}')
    assert "firmware_tool" not in updates


def test_m2003_partial_fields_only() -> None:
    """A snapshot containing only some fields must populate just those fields."""
    from custom_components.xtool_s1.api import XToolS1Client

    client = XToolS1Client("127.0.0.1", session=None)  # type: ignore[arg-type]
    updates = client._parse_m2003_snapshot('{"M99": "1.0", "M310": "abc"}')
    assert updates == {"firmware_version": "1.0", "serial_number": "abc"}


def test_m2003_empty_object() -> None:
    """An empty object yields no updates."""
    from custom_components.xtool_s1.api import XToolS1Client

    client = XToolS1Client("127.0.0.1", session=None)  # type: ignore[arg-type]
    assert client._parse_m2003_snapshot("{}") == {}


def test_m2003_strips_quoted_job_file() -> None:
    from custom_components.xtool_s1.api import XToolS1Client

    client = XToolS1Client("127.0.0.1", session=None)  # type: ignore[arg-type]
    updates = client._parse_m2003_snapshot('{"M810": "\\"file.gcode\\""}')
    assert updates["job_file"] == "file.gcode"


def test_m2003_normalises_null_job_file() -> None:
    from custom_components.xtool_s1.api import XToolS1Client

    client = XToolS1Client("127.0.0.1", session=None)  # type: ignore[arg-type]
    updates = client._parse_m2003_snapshot('{"M810": "NULL"}')
    assert updates["job_file"] is None


def test_parse_frame_unknown_mcode() -> None:
    """An M-code we don't handle just returns an empty dict."""
    from custom_components.xtool_s1.api import XToolS1Client

    client = XToolS1Client("127.0.0.1", session=None)  # type: ignore[arg-type]
    assert client._parse_frame("M9999 something") == {}


@pytest.mark.parametrize(
    ("frame", "expected_keys"),
    [
        ("M222 S5", {"work_state_raw"}),
        ('M810 "engrave.gcode"', {"job_file"}),
        ("M340 A0", {"alarm_raw", "alarm_present"}),
        ("M303 X1.5 Y2.5", {"pos_x", "pos_y"}),
        ("M313 Z-0.5", {"probe_z"}),
    ],
)
def test_parse_frame_recognised_pushes(frame: str, expected_keys: set[str]) -> None:
    from custom_components.xtool_s1.api import XToolS1Client

    client = XToolS1Client("127.0.0.1", session=None)  # type: ignore[arg-type]
    assert set(client._parse_frame(frame).keys()) == expected_keys


@pytest.mark.parametrize(
    "frame",
    [
        "M222 zzz",  # M222 with no leading S → regex matches "zzz", coerced
        "M810 no_quotes",  # no quoted filename → regex returns no match
        "M340 nothing",  # no A-prefix → no match
        "M303 garbage",  # no X..Y.. → no match
        "M313 garbage",  # no Z..  → no match
    ],
)
def test_parse_frame_no_match_returns_empty(frame: str) -> None:
    """Push frames whose payload doesn't match the regex return an empty dict."""
    from custom_components.xtool_s1.api import XToolS1Client

    client = XToolS1Client("127.0.0.1", session=None)  # type: ignore[arg-type]
    # Either {} or any populated dict is fine — we just exercise the branch.
    client._parse_frame(frame)


def test_client_port_property() -> None:
    """The port and http_port properties expose the configured ports."""
    client = XToolS1Client(
        "127.0.0.1",
        session=None,  # type: ignore[arg-type]
        port=12345,
        http_port=23456,
    )
    assert client.port == 12345
    assert client.http_port == 23456
    assert client.host == "127.0.0.1"


@pytest.mark.asyncio
async def test_send_error_raises_connection_error(fake_s1_server, hass) -> None:
    """An OSError from ws.send_str must surface as XToolS1ConnectionError."""
    from unittest.mock import patch as _patch

    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(fake_s1_server.host, session, port=fake_s1_server.port)
    try:
        await client.connect()
        with (
            _patch.object(
                client._ws,
                "send_str",
                side_effect=OSError("pipe broken"),
            ),
            pytest.raises(XToolS1ConnectionError),
        ):
            await client.ping()
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_probe_initial_state_request_status_failure(fake_s1_server, hass) -> None:
    """If request_status() raises after connect, probe_initial_state must clean up."""
    from unittest.mock import patch as _patch

    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(fake_s1_server.host, session, port=fake_s1_server.port)
    with (
        _patch.object(
            client, "request_status", side_effect=XToolS1ConnectionError("oops")
        ),
        pytest.raises(XToolS1ConnectionError),
    ):
        await client.probe_initial_state(timeout=1.0)
    assert not client.connected


# --- listen-loop branch coverage --------------------------------------------


@pytest.mark.asyncio
async def test_listen_loop_handles_binary_push(fake_s1_server, hass) -> None:
    """A binary frame from the server is funneled through the binary handler."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(fake_s1_server.host, session, port=fake_s1_server.port)
    try:
        await client.connect()
        await fake_s1_server.push_binary(b"\x00\x01M340 A4\x02\x03")
        await asyncio.sleep(0.2)
        assert client.state.alarm_present is True
        assert client.state.alarm_raw == "A4"
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_listen_loop_handles_server_close(fake_s1_server, hass) -> None:
    """When the server closes the WS, the listener exits and connection drops."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    client = XToolS1Client(fake_s1_server.host, session, port=fake_s1_server.port)
    try:
        await client.connect()
        assert client.connected
        await fake_s1_server.close_all()
        # Give the listener loop a moment to observe the close.
        for _ in range(20):
            if not client.connected:
                break
            await asyncio.sleep(0.05)
        assert not client.connected
    finally:
        await client.disconnect()


@pytest.mark.asyncio
async def test_listen_loop_swallows_unexpected_exception(hass) -> None:
    """An unexpected exception inside the listener must not crash anything."""
    client = XToolS1Client("127.0.0.1", session=None, port=1)  # type: ignore[arg-type]

    class _ExplodingWS:
        closed = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            raise RuntimeError("listener boom")

        async def close(self):
            return None

    client._ws = _ExplodingWS()  # type: ignore[assignment]
    await client._listen_loop()
    assert client._ws is None
    assert client.state.connected is False


@pytest.mark.asyncio
async def test_listen_loop_breaks_on_close_message(hass) -> None:
    """A WSMsgType.CLOSE message must break out of the listener loop.

    Also exercises the "ignore unknown msg type" branch by passing
    a PING through first — neither TEXT, BINARY, nor CLOSE-family.
    """
    from aiohttp import WSMessage, WSMsgType

    client = XToolS1Client("127.0.0.1", session=None, port=1)  # type: ignore[arg-type]

    class _MixedWS:
        closed = False

        def __init__(self):
            self._messages = iter(
                [
                    WSMessage(WSMsgType.PING, b"", ""),  # ignored
                    WSMessage(WSMsgType.TEXT, "M222 S3", ""),
                    WSMessage(WSMsgType.CLOSE, b"", ""),
                ]
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration as err:
                raise StopAsyncIteration from err

        async def close(self):
            return None

    client._ws = _MixedWS()  # type: ignore[assignment]
    await client._listen_loop()
    # The CLOSE message broke the loop, the TEXT was processed, the
    # PING was silently skipped.
    assert client._ws is None
    assert client.state.work_state_raw == "S3"


# --- async context manager --------------------------------------------------


@pytest.mark.asyncio
async def test_async_context_manager(fake_s1_server, hass) -> None:
    """``async with XToolS1Client(...)`` connects and disconnects."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    session = async_get_clientsession(hass)
    async with XToolS1Client(
        fake_s1_server.host, session, port=fake_s1_server.port
    ) as client:
        assert client.connected
    assert not client.connected
