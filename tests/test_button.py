"""Tests for the xtool_s1 button platform (Stop / Pause / Resume)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.xtool_s1.api import XToolS1ConnectionError
from custom_components.xtool_s1.const import (
    BUTTON_PAUSE,
    BUTTON_RESUME,
    BUTTON_STOP,
    DOMAIN,
    MCODE_PAUSE,
    MCODE_RESUME_BEST_EFFORT,
)

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
async def test_button_platform_creates_three_buttons(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """The button platform exposes Stop, Pause and Resume."""
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)
    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    for key in (BUTTON_STOP, BUTTON_PAUSE, BUTTON_RESUME):
        state = hass.states.get(f"button.xtool_s1_{key}")
        assert state is not None, f"button.xtool_s1_{key} missing"


@pytest.mark.asyncio
async def test_stop_button_press_sends_m108(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """Pressing Stop posts ``M108`` to the HTTP /cmd gateway."""
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)
    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.xtool_s1_stop"},
            blocking=True,
        )
        match = await fake_s1_server.wait_for_http_received(
            lambda line: line == "M108", timeout=1.0
        )
        assert match == "M108"


@pytest.mark.asyncio
async def test_pause_button_press_sends_m22_s1(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """Pressing Pause posts the verified ``M22 S1`` trigger."""
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)
    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.xtool_s1_pause"},
            blocking=True,
        )
        match = await fake_s1_server.wait_for_http_received(
            lambda line: line == MCODE_PAUSE, timeout=1.0
        )
        assert match == MCODE_PAUSE


@pytest.mark.asyncio
async def test_resume_button_press_sends_best_effort_m22_s2(
    hass: HomeAssistant, fake_s1_server
) -> None:
    """Pressing Resume posts the best-effort ``M22 S2`` payload."""
    entry = _entry(fake_s1_server.host)
    entry.add_to_hass(hass)
    with patch_ports(fake_s1_server):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": "button.xtool_s1_resume"},
            blocking=True,
        )
        match = await fake_s1_server.wait_for_http_received(
            lambda line: line == MCODE_RESUME_BEST_EFFORT, timeout=1.0
        )
        assert match == MCODE_RESUME_BEST_EFFORT


@pytest.mark.asyncio
async def test_button_press_connection_error_raises_home_assistant_error() -> None:
    """A failing HTTP send is wrapped in HomeAssistantError."""
    from custom_components.xtool_s1.button import (
        BUTTON_DESCRIPTIONS,
        XToolS1Button,
    )

    description = next(d for d in BUTTON_DESCRIPTIONS if d.key == BUTTON_STOP)
    coordinator = MagicMock()
    coordinator.client = MagicMock()
    coordinator.client.stop_job = AsyncMock(side_effect=XToolS1ConnectionError("boom"))
    button = XToolS1Button.__new__(XToolS1Button)
    button._attr_translation_key = description.translation_key
    button.entity_description = description
    button.coordinator = coordinator
    with pytest.raises(HomeAssistantError, match="stop"):
        await button.async_press()
