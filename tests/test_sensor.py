"""Tests for the xtool_s1 sensor entities."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.const import CONF_HOST, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xtool_s1.const import DOMAIN

from .const import MOCK_SERIAL


@pytest.mark.asyncio
async def test_all_expected_sensors_created(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """The expected set of sensor entities is created and disabled flags honored."""
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

    registry = er.async_get(hass)
    entities = er.async_entries_for_config_entry(registry, entry.entry_id)
    sensors = {
        e.unique_id.split("_", 1)[1]: e for e in entities if e.domain == "sensor"
    }

    expected = {
        "status",
        "firmware_version",
        "firmware_aux_1",
        "firmware_aux_2",
        "firmware_tool",
        "serial_number",
        "tool_type",
        "job_file",
        "position_x",
        "position_y",
        "probe_z",
        "light_brightness",
    }
    assert set(sensors.keys()) == expected

    # serial_number, probe_z and the auxiliary firmwares are disabled by default.
    for key in (
        "serial_number",
        "probe_z",
        "firmware_aux_1",
        "firmware_aux_2",
        "firmware_tool",
    ):
        assert sensors[key].disabled_by is er.RegistryEntryDisabler.INTEGRATION

    # Diagnostic category for the right ones.
    diag = {
        "firmware_version",
        "firmware_aux_1",
        "firmware_aux_2",
        "firmware_tool",
        "serial_number",
        "tool_type",
        "probe_z",
    }
    for key, entry_obj in sensors.items():
        if key in diag:
            assert entry_obj.entity_category is EntityCategory.DIAGNOSTIC
        else:
            assert (
                entry_obj.entity_category is None
                or entry_obj.entity_category != EntityCategory.DIAGNOSTIC
            )


@pytest.mark.asyncio
async def test_status_sensor_native_value(
    hass: HomeAssistant, fake_s1_server_running
) -> None:
    """The status sensor maps M222 codes to the enum values."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="x",
        unique_id=MOCK_SERIAL,
        data={CONF_HOST: fake_s1_server_running.host},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.xtool_s1.const.WS_PORT", fake_s1_server_running.port):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("sensor.xtool_s1_status")
    assert state is not None
    assert state.state == "running"


@pytest.mark.asyncio
async def test_position_and_fan_values(
    hass: HomeAssistant, fake_s1_server_running
) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="x",
        unique_id=MOCK_SERIAL,
        data={CONF_HOST: fake_s1_server_running.host},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.xtool_s1.const.WS_PORT", fake_s1_server_running.port):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    pos_x = hass.states.get("sensor.xtool_s1_position_x")
    pos_y = hass.states.get("sensor.xtool_s1_position_y")
    light = hass.states.get("sensor.xtool_s1_light_brightness")
    assert pos_x is not None and float(pos_x.state) == -12.5
    assert pos_y is not None and float(pos_y.state) == 45.2
    # M13 A/B is the fill-light brightness, not fans (see api.py docstring).
    # The sensor exposes the A channel (both channels carry the same value
    # via the app, but the fixture differs to keep the helpers honest).
    assert light is not None and int(light.state) == 85


@pytest.mark.asyncio
async def test_status_value_when_state_is_none() -> None:
    """The status value_fn returns 'unknown' when work_state_raw is None."""
    from custom_components.xtool_s1.api import XToolS1State
    from custom_components.xtool_s1.sensor import _status_value

    assert _status_value(XToolS1State()) == "unknown"


@pytest.mark.asyncio
async def test_status_value_for_unknown_code() -> None:
    """An unknown S-code falls back to 'unknown'."""
    from custom_components.xtool_s1.api import XToolS1State
    from custom_components.xtool_s1.sensor import _status_value

    assert _status_value(XToolS1State(work_state_raw="S99")) == "unknown"


def test_sensor_native_value_when_data_is_none() -> None:
    """A sensor returns None native_value when the coordinator has no data yet."""
    from unittest.mock import MagicMock

    from custom_components.xtool_s1.const import SENSOR_STATUS
    from custom_components.xtool_s1.sensor import (
        SENSOR_DESCRIPTIONS,
        XToolS1Sensor,
    )

    description = next(d for d in SENSOR_DESCRIPTIONS if d.key == SENSOR_STATUS)
    coordinator = MagicMock()
    coordinator.data = None
    sensor = XToolS1Sensor.__new__(XToolS1Sensor)
    sensor._attr_translation_key = description.translation_key
    sensor.entity_description = description
    sensor.coordinator = coordinator
    assert sensor.native_value is None


def test_entity_unavailable_when_data_is_none() -> None:
    """The entity base class reports unavailable when data is None."""
    from unittest.mock import MagicMock

    from custom_components.xtool_s1.entity import XToolS1Entity

    coordinator = MagicMock()
    coordinator.last_update_success = True
    coordinator.data = None
    entity = XToolS1Entity.__new__(XToolS1Entity)
    entity._attr_translation_key = "status"
    entity.coordinator = coordinator
    # super().available is True (last_update_success), but data is None,
    # so available falls through to False.
    assert entity.available is False


def test_entity_unavailable_when_super_available_false() -> None:
    """Entity propagates the coordinator's last_update_success failure."""
    from unittest.mock import MagicMock

    from custom_components.xtool_s1.entity import XToolS1Entity

    coordinator = MagicMock()
    coordinator.last_update_success = False
    coordinator.data = MagicMock(connected=True)
    entity = XToolS1Entity.__new__(XToolS1Entity)
    entity._attr_translation_key = "status"
    entity.coordinator = coordinator
    # Force CoordinatorEntity.available to return False by mocking it.
    entity.hass = MagicMock()
    entity.hass.is_running = True
    # super().available reads coordinator.last_update_success → False.
    assert entity.available is False


@pytest.mark.asyncio
async def test_job_file_normalised(hass: HomeAssistant, fake_s1_server_running) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="x",
        unique_id=MOCK_SERIAL,
        data={CONF_HOST: fake_s1_server_running.host},
    )
    entry.add_to_hass(hass)
    with patch("custom_components.xtool_s1.const.WS_PORT", fake_s1_server_running.port):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    job = hass.states.get("sensor.xtool_s1_job_file")
    assert job is not None
    assert job.state == "my_engraving.gcode"
