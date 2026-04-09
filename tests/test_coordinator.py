"""Tests for the xtool_s1 coordinator (push + watchdog)."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xtool_s1.api import (
    XToolS1Client,
    XToolS1ConnectionError,
    XToolS1State,
)
from custom_components.xtool_s1.const import DOMAIN
from custom_components.xtool_s1.coordinator import (
    MODE_COEXIST,
    MODE_NORMAL,
    MODE_OFFLINE,
    XToolS1Coordinator,
)

from .const import MOCK_HOST, MOCK_SERIAL


def _mock_entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="xTool S1 (test)",
        unique_id=MOCK_SERIAL,
        data={CONF_HOST: MOCK_HOST},
    )


async def _build(
    hass: HomeAssistant, host: str, port: int
) -> tuple[XToolS1Coordinator, XToolS1Client, MockConfigEntry]:
    entry = _mock_entry()
    entry.add_to_hass(hass)
    session = async_get_clientsession(hass)
    client = XToolS1Client(host, session, port=port)
    coordinator = XToolS1Coordinator(hass, entry, client)
    return coordinator, client, entry


@pytest.mark.asyncio
async def test_push_state_propagates_to_coordinator(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """A frame pushed by the server lands on coordinator.data."""
    coordinator, _client, _entry = await _build(
        hass, fake_s1_server.host, fake_s1_server.port
    )
    try:
        # Drive the watchdog directly so we don't need an entry SETUP_IN_PROGRESS.
        state = await coordinator._async_update_data()
        coordinator.async_set_updated_data(state)
        assert coordinator.data is not None
        assert coordinator.data.serial_number == MOCK_SERIAL

        # Push a state change from the server side.
        await fake_s1_server.push("M222 S14")
        await asyncio.sleep(0.1)
        assert coordinator.data.work_state_raw == "S14"
    finally:
        await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_first_refresh_failure_returns_power_off_snapshot(
    hass: HomeAssistant,
) -> None:
    """A connect failure returns a power-off snapshot (no UpdateFailed)."""
    entry = _mock_entry()
    entry.add_to_hass(hass)
    session = async_get_clientsession(hass)
    # Point at a port nothing listens on.
    client = XToolS1Client("127.0.0.1", session, port=1)
    coordinator = XToolS1Coordinator(hass, entry, client)
    state = await coordinator._async_update_data()
    assert isinstance(state, XToolS1State)
    assert state.connected is False


@pytest.mark.asyncio
async def test_watchdog_ping_failure_returns_power_off_snapshot(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """If the WS dies between polls, the next poll returns a power-off state."""
    coordinator, client, _ = await _build(
        hass, fake_s1_server.host, fake_s1_server.port
    )
    try:
        await coordinator._async_update_data()
        with patch.object(
            client, "ping", side_effect=XToolS1ConnectionError("dropped")
        ):
            state = await coordinator._async_update_data()
        assert isinstance(state, XToolS1State)
        assert state.connected is False
    finally:
        await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_watchdog_reconnects_on_dead_socket(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """A dead socket triggers reconnect + a fresh status request."""
    coordinator, client, _ = await _build(
        hass, fake_s1_server.host, fake_s1_server.port
    )
    try:
        await coordinator._async_update_data()
        await client.disconnect()
        state = await coordinator._async_update_data()
        assert isinstance(state, XToolS1State)
        assert client.connected
    finally:
        await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_shutdown_unsubscribes_push(hass: HomeAssistant, fake_s1_server) -> None:
    """After shutdown the coordinator no longer receives pushed frames."""
    coordinator, _client, _entry = await _build(
        hass, fake_s1_server.host, fake_s1_server.port
    )
    state = await coordinator._async_update_data()
    coordinator.async_set_updated_data(state)
    last_state = coordinator.data
    await coordinator.async_shutdown()
    # Coordinator data is frozen at the snapshot taken before shutdown.
    assert coordinator.data is last_state


@pytest.mark.asyncio
async def test_watchdog_happy_ping_path(hass: HomeAssistant, fake_s1_server) -> None:
    """The second update tick on a healthy connection just sends a ping."""
    coordinator, _client, _entry = await _build(
        hass, fake_s1_server.host, fake_s1_server.port
    )
    try:
        first = await coordinator._async_update_data()
        assert first.serial_number == MOCK_SERIAL
        # Second tick goes through the connected branch (ping only).
        second = await coordinator._async_update_data()
        assert isinstance(second, XToolS1State)
    finally:
        await coordinator.async_shutdown()


# --- backoff and HTTP heartbeat ---------------------------------------------


async def _build_with_http(
    hass: HomeAssistant, fake_server
) -> tuple[XToolS1Coordinator, XToolS1Client, MockConfigEntry]:
    """Build a coordinator wired to both the WS and HTTP fake server ports."""
    entry = _mock_entry()
    entry.add_to_hass(hass)
    session = async_get_clientsession(hass)
    client = XToolS1Client(
        fake_server.host,
        session,
        port=fake_server.port,
        http_port=fake_server.http_port,
    )
    coordinator = XToolS1Coordinator(hass, entry, client)
    return coordinator, client, entry


@pytest.mark.asyncio
async def test_kick_detection_climbs_backoff(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """A drop within ~10s of connect is treated as a kick and climbs the ladder."""
    coordinator, client, _entry = await _build_with_http(hass, fake_s1_server)
    try:
        await coordinator._async_update_data()
        # Force the client into a "lost connection" state and re-tick.
        with patch.object(client, "ping", side_effect=XToolS1ConnectionError("kicked")):
            await coordinator._async_update_data()
        # The backoff should now be active and the index should be > 0
        assert coordinator._backoff_index > 0
        assert coordinator._is_in_backoff()
    finally:
        await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_http_heartbeat_keeps_entry_available(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """While in backoff, an HTTP heartbeat returns the cached state."""
    coordinator, client, _entry = await _build_with_http(hass, fake_s1_server)
    try:
        await coordinator._async_update_data()
        # Disconnect and force the coordinator into backoff
        await client.disconnect()
        coordinator._next_reconnect_at = float("inf")  # always in backoff
        coordinator._backoff_index = 1

        state = await coordinator._async_update_data()
        assert isinstance(state, XToolS1State)
        # The fake server's HTTP /system?action=mac is alive — heartbeat OK
    finally:
        await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_http_heartbeat_failure_returns_power_off_snapshot(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """If both WS and HTTP fail, returns a power-off snapshot (no error)."""
    coordinator, client, _entry = await _build_with_http(hass, fake_s1_server)
    try:
        await coordinator._async_update_data()
        await client.disconnect()
        coordinator._next_reconnect_at = float("inf")
        with patch.object(
            client,
            "fetch_mac_http",
            side_effect=XToolS1ConnectionError("offline"),
        ):
            state = await coordinator._async_update_data()
        assert isinstance(state, XToolS1State)
        assert state.connected is False
        assert state.light_brightness_a == 0
        assert state.alarm_present is False
    finally:
        await coordinator.async_shutdown()


@pytest.mark.asyncio
async def test_backoff_caps_at_top_of_ladder(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """Repeated kicks should not push the backoff index past the ladder."""
    from custom_components.xtool_s1.const import RECONNECT_BACKOFF_SECONDS

    coordinator, _client, _entry = await _build_with_http(hass, fake_s1_server)
    try:
        # Climb the ladder by manually noting kicks
        for _ in range(len(RECONNECT_BACKOFF_SECONDS) + 5):
            coordinator._note_disconnected(kicked=True)
        assert coordinator._backoff_index == len(RECONNECT_BACKOFF_SECONDS) - 1

        # Same with soft drops
        coordinator._backoff_index = 0
        for _ in range(len(RECONNECT_BACKOFF_SECONDS) + 5):
            coordinator._note_disconnected(kicked=False)
        assert coordinator._backoff_index == len(RECONNECT_BACKOFF_SECONDS) - 1
    finally:
        await coordinator.async_shutdown()


# --- coexist + offline mode coverage ---------------------------------------


def _make_fake_client() -> MagicMock:
    """Build a fully-mocked XToolS1Client suitable for unit-level tests."""
    client = MagicMock()
    client.host = "127.0.0.1"
    client.connected = False
    client.state = XToolS1State()
    client.connect = AsyncMock()
    client.disconnect = AsyncMock()
    client.ping = AsyncMock()
    client.probe_initial_state = AsyncMock(return_value=XToolS1State())
    client.fetch_mac_http = AsyncMock(return_value="aa:bb:cc:dd:ee:ff")
    client.request_stats = AsyncMock()
    client.on_state = MagicMock(return_value=lambda: None)
    return client


def _make_coordinator(hass: HomeAssistant, client: MagicMock) -> XToolS1Coordinator:
    entry = _mock_entry()
    entry.add_to_hass(hass)
    return XToolS1Coordinator(hass, entry, client)


@pytest.mark.asyncio
async def test_async_update_dispatches_to_coexist_tick(hass: HomeAssistant) -> None:
    """When mode is COEXIST, _async_update_data calls the coexist tick."""
    client = _make_fake_client()
    coordinator = _make_coordinator(hass, client)
    coordinator._enter_mode(MODE_COEXIST)

    state = await coordinator._async_update_data()
    assert isinstance(state, XToolS1State)
    client.fetch_mac_http.assert_awaited()


@pytest.mark.asyncio
async def test_async_update_dispatches_to_offline_tick(hass: HomeAssistant) -> None:
    """When mode is OFFLINE, the offline tick recovers via HTTP."""
    client = _make_fake_client()
    coordinator = _make_coordinator(hass, client)
    coordinator._enter_mode(MODE_OFFLINE)

    state = await coordinator._async_update_data()
    assert isinstance(state, XToolS1State)
    # Successful HTTP heartbeat → flipped back to NORMAL.
    assert coordinator.mode == MODE_NORMAL


@pytest.mark.asyncio
async def test_offline_tick_still_failing_returns_power_off(
    hass: HomeAssistant,
) -> None:
    """A still-failing HTTP heartbeat returns a power-off snapshot (no error)."""
    client = _make_fake_client()
    client.fetch_mac_http.side_effect = XToolS1ConnectionError("offline")
    coordinator = _make_coordinator(hass, client)
    coordinator._enter_mode(MODE_OFFLINE)

    state = await coordinator._async_update_data()
    assert isinstance(state, XToolS1State)
    assert state.connected is False
    assert coordinator.mode == MODE_OFFLINE


@pytest.mark.asyncio
async def test_coexist_tick_disconnects_lingering_ws(hass: HomeAssistant) -> None:
    """A leftover WS connection in coexist mode is closed before HTTP heartbeat."""
    client = _make_fake_client()
    client.connected = True
    coordinator = _make_coordinator(hass, client)
    coordinator._enter_mode(MODE_COEXIST)

    await coordinator._coexist_tick()
    client.disconnect.assert_awaited()


@pytest.mark.asyncio
async def test_coexist_tick_http_failure_marks_offline(hass: HomeAssistant) -> None:
    """If HTTP also dies in coexist mode, the coordinator goes offline."""
    client = _make_fake_client()
    client.fetch_mac_http.side_effect = XToolS1ConnectionError("nope")
    coordinator = _make_coordinator(hass, client)
    coordinator._enter_mode(MODE_COEXIST)
    coordinator._http_failing_since = time.monotonic() - 9999

    state = await coordinator._coexist_tick()
    assert isinstance(state, XToolS1State)
    assert state.connected is False
    assert coordinator.mode == MODE_OFFLINE


@pytest.mark.asyncio
async def test_coexist_recovery_attempt_failure_resets_timer(
    hass: HomeAssistant,
) -> None:
    """A failed WS recovery attempt in coexist resets the recovery timer."""
    client = _make_fake_client()
    client.probe_initial_state = AsyncMock(
        side_effect=XToolS1ConnectionError("kicked again")
    )
    coordinator = _make_coordinator(hass, client)
    coordinator._enter_mode(MODE_COEXIST)
    coordinator._mode_changed_at = time.monotonic() - 9999  # force recovery attempt

    state = await coordinator._coexist_tick()
    assert isinstance(state, XToolS1State)
    assert coordinator.mode == MODE_COEXIST
    client.probe_initial_state.assert_awaited()


@pytest.mark.asyncio
async def test_coexist_recovery_attempt_success_returns_to_normal(
    hass: HomeAssistant,
) -> None:
    """A successful WS recovery in coexist mode flips back to Normal."""
    fresh = XToolS1State(serial_number="recovered")
    client = _make_fake_client()
    client.probe_initial_state = AsyncMock(return_value=fresh)
    coordinator = _make_coordinator(hass, client)
    coordinator._enter_mode(MODE_COEXIST)
    coordinator._mode_changed_at = time.monotonic() - 9999

    state = await coordinator._coexist_tick()
    assert state is fresh
    assert coordinator.mode == MODE_NORMAL


@pytest.mark.asyncio
async def test_kick_storm_switches_normal_to_coexist_mid_call(
    hass: HomeAssistant,
) -> None:
    """A ping failure that triggers the kick-storm flips to coexist mid-call."""
    from custom_components.xtool_s1.const import COEXIST_KICK_LIMIT

    client = _make_fake_client()
    client.connected = True
    client.ping = AsyncMock(side_effect=XToolS1ConnectionError("kicked"))
    coordinator = _make_coordinator(hass, client)
    # Prime the kick log so the next disconnect tips over the limit.
    now = time.monotonic()
    for _ in range(COEXIST_KICK_LIMIT):
        coordinator._kick_log.append(now)
    coordinator._connected_at = now  # treat as freshly-connected

    state = await coordinator._async_update_data()
    assert isinstance(state, XToolS1State)
    assert coordinator.mode == MODE_COEXIST


@pytest.mark.asyncio
async def test_kick_storm_during_disconnected_branch_switches_to_coexist(
    hass: HomeAssistant,
) -> None:
    """A failed initial probe inside a kick storm flips into coexist mid-call."""
    from custom_components.xtool_s1.const import COEXIST_KICK_LIMIT

    client = _make_fake_client()
    client.connected = False
    client.probe_initial_state = AsyncMock(
        side_effect=XToolS1ConnectionError("kicked early")
    )
    coordinator = _make_coordinator(hass, client)
    # Prime the kick log so _note_disconnected (kicked=False) tips us over.
    now = time.monotonic()
    for _ in range(COEXIST_KICK_LIMIT):
        coordinator._kick_log.append(now)

    state = await coordinator._async_update_data()
    assert isinstance(state, XToolS1State)
    assert coordinator.mode == MODE_COEXIST


@pytest.mark.asyncio
async def test_maybe_poll_stats_skipped_when_offline(hass: HomeAssistant) -> None:
    """``_maybe_poll_stats`` is a no-op while the coordinator is offline."""
    client = _make_fake_client()
    coordinator = _make_coordinator(hass, client)
    coordinator._enter_mode(MODE_OFFLINE)

    await coordinator._maybe_poll_stats()
    client.request_stats.assert_not_awaited()


@pytest.mark.asyncio
async def test_maybe_poll_stats_runs_when_interval_has_passed(
    hass: HomeAssistant,
) -> None:
    """A poll fires when the last poll was longer than the interval ago."""
    client = _make_fake_client()
    coordinator = _make_coordinator(hass, client)
    # Pretend the last poll happened a long time ago.
    coordinator._last_stats_poll = time.monotonic() - 9999

    await coordinator._maybe_poll_stats()
    client.request_stats.assert_awaited()
    # _last_stats_poll is bumped forward on success.
    assert coordinator._last_stats_poll > time.monotonic() - 9999


@pytest.mark.asyncio
async def test_maybe_poll_stats_skips_when_recently_polled(hass: HomeAssistant) -> None:
    """Subsequent calls inside the interval are skipped silently."""
    client = _make_fake_client()
    coordinator = _make_coordinator(hass, client)
    coordinator._last_stats_poll = time.monotonic()

    await coordinator._maybe_poll_stats()
    client.request_stats.assert_not_awaited()


@pytest.mark.asyncio
async def test_maybe_poll_stats_swallows_connection_error(
    hass: HomeAssistant,
) -> None:
    """A failing M2008 send is swallowed (no exception escapes)."""
    client = _make_fake_client()
    client.request_stats = AsyncMock(side_effect=XToolS1ConnectionError("nope"))
    coordinator = _make_coordinator(hass, client)
    coordinator._last_stats_poll = time.monotonic() - 9999
    failing_marker = coordinator._last_stats_poll

    await coordinator._maybe_poll_stats()
    client.request_stats.assert_awaited()
    # last_stats_poll is NOT advanced on failure.
    assert coordinator._last_stats_poll == failing_marker


@pytest.mark.asyncio
async def test_http_heartbeat_treats_none_as_failure(hass: HomeAssistant) -> None:
    """A None response from fetch_mac_http counts as a heartbeat failure."""
    client = _make_fake_client()
    client.fetch_mac_http = AsyncMock(return_value=None)
    coordinator = _make_coordinator(hass, client)

    assert await coordinator._http_heartbeat() is False
    assert coordinator._http_failing_since is not None


@pytest.mark.asyncio
async def test_mark_http_ok_recovers_from_offline(hass: HomeAssistant) -> None:
    """A successful heartbeat after going offline switches back to Normal."""
    client = _make_fake_client()
    coordinator = _make_coordinator(hass, client)
    coordinator._enter_mode(MODE_OFFLINE)

    coordinator._mark_http_ok()
    assert coordinator.mode == MODE_NORMAL


@pytest.mark.asyncio
async def test_power_off_snapshot_flips_to_offline(hass: HomeAssistant) -> None:
    """``_power_off_snapshot`` enters OFFLINE after the failure-window expires."""
    client = _make_fake_client()
    coordinator = _make_coordinator(hass, client)
    coordinator._http_failing_since = time.monotonic() - 9999

    state = coordinator._power_off_snapshot()
    assert isinstance(state, XToolS1State)
    assert state.connected is False
    assert state.light_brightness_a == 0
    assert state.alarm_present is False
    assert coordinator.mode == MODE_OFFLINE


@pytest.mark.asyncio
async def test_http_reachable_property(hass: HomeAssistant) -> None:
    """``http_reachable`` reflects the offline mode flag."""
    client = _make_fake_client()
    coordinator = _make_coordinator(hass, client)
    assert coordinator.http_reachable is True
    coordinator._enter_mode(MODE_OFFLINE)
    assert coordinator.http_reachable is False


@pytest.mark.asyncio
async def test_normal_tick_disconnected_probe_fails_http_recovers(
    hass: HomeAssistant,
) -> None:
    """If WS probe fails but HTTP heartbeat succeeds, return cached state."""
    client = _make_fake_client()
    client.connected = False
    client.probe_initial_state = AsyncMock(
        side_effect=XToolS1ConnectionError("ws unreachable")
    )
    coordinator = _make_coordinator(hass, client)

    state = await coordinator._async_update_data()
    assert isinstance(state, XToolS1State)
    # Still in normal mode (no kick storm), but WS is offline.
    assert coordinator.mode == MODE_NORMAL
    client.fetch_mac_http.assert_awaited()


@pytest.mark.asyncio
async def test_enter_mode_noop_when_unchanged(hass: HomeAssistant) -> None:
    """Re-entering the same mode does not move the timestamp."""
    client = _make_fake_client()
    coordinator = _make_coordinator(hass, client)
    initial = coordinator._mode_changed_at
    coordinator._enter_mode(MODE_NORMAL)
    assert coordinator._mode_changed_at == initial
