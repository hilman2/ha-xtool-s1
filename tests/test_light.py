"""Tests for the xtool_s1 fill-light entity."""

from __future__ import annotations

from unittest.mock import patch

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode
from homeassistant.const import CONF_HOST, STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xtool_s1.api import XToolS1ConnectionError
from custom_components.xtool_s1.const import DOMAIN
from custom_components.xtool_s1.light import _ha_to_laser, _laser_to_ha

from .conftest import patch_ports
from .const import MOCK_SERIAL


def _entry(host: str) -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        title="x",
        unique_id=MOCK_SERIAL,
        data={CONF_HOST: host},
    )


# --- pure helper coverage --------------------------------------------------


@pytest.mark.parametrize(
    ("ha_value", "laser_value"),
    [(0, 0), (255, 100), (128, 50), (1, 0), (51, 20)],
)
def test_ha_to_laser(ha_value: int, laser_value: int) -> None:
    assert _ha_to_laser(ha_value) == laser_value


@pytest.mark.parametrize(
    ("laser_value", "ha_value"),
    [(0, 1), (100, 255), (50, 128), (1, 3)],
)
def test_laser_to_ha(laser_value: int, ha_value: int) -> None:
    assert _laser_to_ha(laser_value) == ha_value


# --- end-to-end against the fake server ------------------------------------


@pytest.mark.asyncio
async def test_light_initial_state_off(hass: HomeAssistant, fake_s1_server) -> None:
    """The idle fixture has M13 A0 B0 → light is off."""
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)
    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("light.xtool_s1_fill_light")
    assert state is not None
    assert state.state == STATE_OFF
    assert state.attributes.get("supported_color_modes") == [ColorMode.BRIGHTNESS]
    assert state.attributes.get("color_mode") in (None, ColorMode.BRIGHTNESS)


@pytest.mark.asyncio
async def test_light_initial_state_on(
    hass: HomeAssistant, fake_s1_server_running
) -> None:
    """The running fixture has M13 A85 → light is on at 85 %."""
    entry = _entry(fake_s1_server_running.host)
    entry.add_to_hass(hass)
    with patch_ports(fake_s1_server_running):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    state = hass.states.get("light.xtool_s1_fill_light")
    assert state is not None
    assert state.state == STATE_ON
    # 85 / 100 * 255 ≈ 217
    assert state.attributes[ATTR_BRIGHTNESS] == 217


@pytest.mark.asyncio
async def test_light_turn_on_with_brightness(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """turn_on with brightness sends M13 with the converted value."""
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)
    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.xtool_s1_fill_light", "brightness": 128},
            blocking=True,
        )
        await hass.async_block_till_done()

    # 128 → 50 % on the laser scale
    assert (
        await fake_s1_server.wait_for_http_received(lambda f: f == "M13 A50 B50")
    ) is not None
    state = hass.states.get("light.xtool_s1_fill_light")
    assert state.state == STATE_ON
    assert state.attributes[ATTR_BRIGHTNESS] == 128


@pytest.mark.asyncio
async def test_light_turn_on_no_brightness_uses_full(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """turn_on without brightness while off sends M13 A100 B100."""
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)
    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.xtool_s1_fill_light"},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert (
        await fake_s1_server.wait_for_http_received(lambda f: f == "M13 A100 B100")
    ) is not None


@pytest.mark.asyncio
async def test_light_turn_on_no_brightness_keeps_current(
    hass: HomeAssistant, fake_s1_server_running
) -> None:
    """turn_on without brightness while already on keeps the current value."""
    entry = _entry(fake_s1_server_running.host)
    entry.add_to_hass(hass)
    with patch_ports(fake_s1_server_running):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        # Drop everything received during setup so the assertion is clean.
        fake_s1_server_running.http_received.clear()

        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": "light.xtool_s1_fill_light"},
            blocking=True,
        )
        await hass.async_block_till_done()

    # Light is at 85 — turn_on without brightness should keep 85.
    assert (
        await fake_s1_server_running.wait_for_http_received(
            lambda f: f == "M13 A85 B85"
        )
    ) is not None


@pytest.mark.asyncio
async def test_light_turn_on_brightness_zero_turns_off() -> None:
    """turn_on(brightness=0) is treated as turn_off (direct unit test).

    HA's service schema rejects brightness=0 from the front-end, but
    the entity method must still handle it defensively in case an
    automation calls it directly.
    """
    from unittest.mock import AsyncMock, MagicMock

    from custom_components.xtool_s1.light import XToolS1FillLight

    coordinator = MagicMock()
    coordinator.data = MagicMock(light_brightness_a=85)
    coordinator.client = MagicMock()
    coordinator.client.set_light_brightness = AsyncMock()
    coordinator.client.state = MagicMock()
    coordinator.async_set_updated_data = MagicMock()

    light = XToolS1FillLight.__new__(XToolS1FillLight)
    light.coordinator = coordinator
    await light.async_turn_on(brightness=0)
    coordinator.client.set_light_brightness.assert_awaited_once_with(0)


@pytest.mark.asyncio
async def test_light_turn_off(hass: HomeAssistant, fake_s1_server_running) -> None:
    """turn_off sends M13 A0 B0."""
    entry = _entry(fake_s1_server_running.host)
    entry.add_to_hass(hass)
    with patch_ports(fake_s1_server_running):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        fake_s1_server_running.http_received.clear()
        await hass.services.async_call(
            "light",
            "turn_off",
            {"entity_id": "light.xtool_s1_fill_light"},
            blocking=True,
        )
        await hass.async_block_till_done()

    assert (
        await fake_s1_server_running.wait_for_http_received(lambda f: f == "M13 A0 B0")
    ) is not None
    state = hass.states.get("light.xtool_s1_fill_light")
    assert state.state == STATE_OFF


# --- direct unit tests for the data-is-None paths --------------------------


def test_light_is_on_returns_none_when_no_data() -> None:
    """is_on returns None when the coordinator has no data yet."""
    from unittest.mock import MagicMock

    from custom_components.xtool_s1.light import XToolS1FillLight

    coordinator = MagicMock()
    coordinator.data = None
    light = XToolS1FillLight.__new__(XToolS1FillLight)
    light.coordinator = coordinator
    assert light.is_on is None
    assert light.brightness is None


@pytest.mark.asyncio
async def test_light_turn_on_no_brightness_no_data() -> None:
    """turn_on while data is None falls back to 100 % anyway."""
    from unittest.mock import AsyncMock, MagicMock

    from custom_components.xtool_s1.light import XToolS1FillLight

    coordinator = MagicMock()
    coordinator.data = None
    coordinator.client = MagicMock()
    coordinator.client.set_light_brightness = AsyncMock()
    coordinator.client.state = MagicMock()
    coordinator.async_set_updated_data = MagicMock()

    light = XToolS1FillLight.__new__(XToolS1FillLight)
    light.coordinator = coordinator
    await light.async_turn_on()
    coordinator.client.set_light_brightness.assert_awaited_once_with(100)


@pytest.mark.asyncio
async def test_light_turn_on_failure_raises_home_assistant_error(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """A connection failure during set_light_brightness becomes HomeAssistantError."""
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)
    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        with (
            patch(
                "custom_components.xtool_s1.api.XToolS1Client.set_light_brightness",
                side_effect=XToolS1ConnectionError("offline"),
            ),
            pytest.raises(HomeAssistantError),
        ):
            await hass.services.async_call(
                "light",
                "turn_on",
                {"entity_id": "light.xtool_s1_fill_light", "brightness": 200},
                blocking=True,
            )
