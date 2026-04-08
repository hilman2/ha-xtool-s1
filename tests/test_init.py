"""Tests for the xtool_s1 setup / unload flow."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xtool_s1.api import XToolS1ConnectionError
from custom_components.xtool_s1.const import DOMAIN
from custom_components.xtool_s1.coordinator import XToolS1RuntimeData

from .const import MOCK_SERIAL


def _patch_probe(state):
    return patch(
        "custom_components.xtool_s1.api.XToolS1Client.probe_initial_state",
        return_value=state,
    )


def _patch_connect_ok():
    """Patch all the WS interactions used by setup so no real socket is opened."""
    return [
        patch(
            "custom_components.xtool_s1.api.XToolS1Client.connect", return_value=None
        ),
        patch(
            "custom_components.xtool_s1.api.XToolS1Client.request_status",
            return_value=None,
        ),
        patch("custom_components.xtool_s1.api.XToolS1Client.ping", return_value=None),
        patch(
            "custom_components.xtool_s1.api.XToolS1Client.disconnect", return_value=None
        ),
        patch(
            "custom_components.xtool_s1.api.XToolS1Client.connected",
            new_callable=lambda: property(lambda self: True),
        ),
    ]


async def test_setup_and_unload(hass: HomeAssistant, fake_s1_server) -> None:
    """A real fake server end-to-end: setup, runtime_data populated, unload."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"xTool S1 ({fake_s1_server.host})",
        unique_id=MOCK_SERIAL,
        data={CONF_HOST: fake_s1_server.host},
    )
    entry.add_to_hass(hass)

    with patch("custom_components.xtool_s1.const.WS_PORT", fake_s1_server.port):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert isinstance(entry.runtime_data, XToolS1RuntimeData)
    assert entry.runtime_data.coordinator.data is not None
    assert entry.runtime_data.coordinator.data.serial_number == MOCK_SERIAL

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_setup_retry_on_connect_failure(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    """A failed first refresh must put the entry into SETUP_RETRY."""
    mock_config_entry.add_to_hass(hass)
    with patch(
        "custom_components.xtool_s1.api.XToolS1Client.connect",
        side_effect=XToolS1ConnectionError("offline"),
    ):
        await hass.config_entries.async_setup(mock_config_entry.entry_id)
        await hass.async_block_till_done()
    assert mock_config_entry.state is ConfigEntryState.SETUP_RETRY


async def test_unload_skips_shutdown_when_platforms_fail(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """If unload_platforms returns False, async_shutdown is not called."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="x",
        unique_id=MOCK_SERIAL,
        data={CONF_HOST: fake_s1_server.host},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.xtool_s1.const.WS_PORT", fake_s1_server.port):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with (
            patch(
                "homeassistant.config_entries.ConfigEntries.async_unload_platforms",
                return_value=False,
            ),
            patch(
                "custom_components.xtool_s1.coordinator.XToolS1Coordinator.async_shutdown"
            ) as mock_shutdown,
        ):
            unload_ok = await hass.config_entries.async_unload(entry.entry_id)
        assert unload_ok is False
        mock_shutdown.assert_not_called()


async def test_update_listener_reloads_entry(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """Updating an entry's options fires the registered reload listener."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=f"xTool S1 ({fake_s1_server.host})",
        unique_id=MOCK_SERIAL,
        data={CONF_HOST: fake_s1_server.host},
    )
    entry.add_to_hass(hass)

    with patch("custom_components.xtool_s1.const.WS_PORT", fake_s1_server.port):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        first_runtime = entry.runtime_data

        # Touch the entry's *options* (not data — host stays the same so
        # the reload talks to the same fake server).
        hass.config_entries.async_update_entry(entry, options={"_test": True})
        await hass.async_block_till_done()

        assert entry.state is ConfigEntryState.LOADED
        assert entry.runtime_data is not first_runtime
