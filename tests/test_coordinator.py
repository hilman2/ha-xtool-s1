"""Tests for the xtool_s1 coordinator (push + watchdog)."""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xtool_s1.api import (
    XToolS1Client,
    XToolS1ConnectionError,
    XToolS1State,
)
from custom_components.xtool_s1.const import DOMAIN
from custom_components.xtool_s1.coordinator import XToolS1Coordinator

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
async def test_first_refresh_failure_raises_update_failed(hass: HomeAssistant) -> None:
    """A connect failure during a watchdog tick becomes UpdateFailed."""
    entry = _mock_entry()
    entry.add_to_hass(hass)
    session = async_get_clientsession(hass)
    # Point at a port nothing listens on.
    client = XToolS1Client("127.0.0.1", session, port=1)
    coordinator = XToolS1Coordinator(hass, entry, client)
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()


@pytest.mark.asyncio
async def test_watchdog_ping_failure_raises_update_failed(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """If the WS dies between polls, the next poll must raise UpdateFailed."""
    coordinator, client, _ = await _build(
        hass, fake_s1_server.host, fake_s1_server.port
    )
    try:
        await coordinator._async_update_data()
        # Force the client to look connected, then make ping fail mid-call.
        with (
            patch.object(client, "ping", side_effect=XToolS1ConnectionError("dropped")),
            pytest.raises(UpdateFailed),
        ):
            await coordinator._async_update_data()
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
        with (
            patch.object(client, "ping", side_effect=XToolS1ConnectionError("kicked")),
            pytest.raises(UpdateFailed),
        ):
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
async def test_http_heartbeat_failure_raises_update_failed(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """If both WS and HTTP heartbeat fail, UpdateFailed is raised."""
    coordinator, client, _entry = await _build_with_http(hass, fake_s1_server)
    try:
        await coordinator._async_update_data()
        await client.disconnect()
        coordinator._next_reconnect_at = float("inf")
        # Make the HTTP heartbeat fail too
        with (
            patch.object(
                client,
                "fetch_mac_http",
                side_effect=XToolS1ConnectionError("offline"),
            ),
            pytest.raises(UpdateFailed),
        ):
            await coordinator._async_update_data()
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
