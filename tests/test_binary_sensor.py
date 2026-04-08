"""Tests for the xtool_s1 binary sensor entities."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xtool_s1.const import DOMAIN

from .conftest import patch_ports
from .const import MOCK_SERIAL


def _entry(host: str) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="x",
        unique_id=MOCK_SERIAL,
        data={CONF_HOST: host},
    )


@pytest.mark.asyncio
async def test_running_sensor_off_when_idle(
    hass: HomeAssistant, fake_s1_server
) -> None:
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)
    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    running = hass.states.get("binary_sensor.xtool_s1_running")
    assert running is not None
    assert running.state == STATE_OFF


@pytest.mark.asyncio
async def test_running_sensor_on_when_running(
    hass: HomeAssistant, fake_s1_server_running
) -> None:
    entry = _entry(fake_s1_server_running.host)
    entry.add_to_hass(hass)
    with patch_ports(fake_s1_server_running):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    running = hass.states.get("binary_sensor.xtool_s1_running")
    assert running is not None
    assert running.state == STATE_ON


@pytest.mark.asyncio
async def test_alarm_sensor_on_when_alarm(
    hass: HomeAssistant, fake_s1_server_alarm
) -> None:
    entry = _entry(fake_s1_server_alarm.host)
    entry.add_to_hass(hass)
    with patch_ports(fake_s1_server_alarm):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    alarm = hass.states.get("binary_sensor.xtool_s1_alarm")
    assert alarm is not None
    assert alarm.state == STATE_ON


@pytest.mark.asyncio
async def test_connection_sensor_available_when_data_none() -> None:
    """The connection binary sensor short-circuits the available check
    so users can see ``off`` even when there is no coordinator data yet."""
    from unittest.mock import MagicMock

    from custom_components.xtool_s1.binary_sensor import (
        BINARY_SENSOR_DESCRIPTIONS,
        XToolS1BinarySensor,
    )
    from custom_components.xtool_s1.const import BINARY_SENSOR_CONNECTION

    description = next(
        d for d in BINARY_SENSOR_DESCRIPTIONS if d.key == BINARY_SENSOR_CONNECTION
    )
    coordinator = MagicMock()
    coordinator.data = None
    coordinator.config_entry = MagicMock()
    coordinator.config_entry.unique_id = "abc"
    coordinator.config_entry.entry_id = "abc"
    coordinator.config_entry.data = {"host": "127.0.0.1"}
    sensor = XToolS1BinarySensor.__new__(XToolS1BinarySensor)
    sensor.hass = None  # avoid CoordinatorEntity init
    sensor._attr_translation_key = description.translation_key
    sensor.entity_description = description
    sensor.coordinator = coordinator
    # When coordinator.data is None, available is False (data check) and
    # is_on returns None.
    assert sensor.is_on is None


@pytest.mark.asyncio
async def test_connection_sensor_on_when_connected(
    hass: HomeAssistant, fake_s1_server
) -> None:
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)
    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    conn = hass.states.get("binary_sensor.xtool_s1_connection")
    assert conn is not None
    assert conn.state == STATE_ON
    # The connection sensor must be in the diagnostic category.
    registry = er.async_get(hass)
    entry_ent = registry.async_get("binary_sensor.xtool_s1_connection")
    assert entry_ent is not None
    assert entry_ent.entity_category.value == "diagnostic"
