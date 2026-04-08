"""Light platform for the xTool S1 — controls the internal fill light."""

from __future__ import annotations

from typing import Any

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ColorMode,
    LightEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import XToolS1ConnectionError
from .const import LIGHT_FILL_LIGHT
from .coordinator import XToolS1ConfigEntry
from .entity import XToolS1Entity

PARALLEL_UPDATES = 1  # the laser only has one light, serialise writes


def _ha_to_laser(ha_brightness: int) -> int:
    """Convert HA's 0-255 scale to the S1's 0-100 scale."""
    return max(0, min(100, round(ha_brightness * 100 / 255)))


def _laser_to_ha(laser_brightness: int) -> int:
    """Convert the S1's 0-100 scale to HA's 0-255 scale."""
    return max(1, min(255, round(laser_brightness * 255 / 100)))


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XToolS1ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the xTool S1 fill light from a config entry."""
    async_add_entities([XToolS1FillLight(entry.runtime_data.coordinator)])


class XToolS1FillLight(XToolS1Entity, LightEntity):
    """Dimmable interior fill light driven by the M13 G-code."""

    _attr_translation_key = LIGHT_FILL_LIGHT
    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}

    @property
    def is_on(self) -> bool | None:
        """Return whether the light is currently on."""
        data = self.coordinator.data
        if data is None or data.light_brightness_a is None:
            return None
        return data.light_brightness_a > 0

    @property
    def brightness(self) -> int | None:
        """Return the current brightness on HA's 0-255 scale."""
        data = self.coordinator.data
        if data is None or data.light_brightness_a is None:
            return None
        return _laser_to_ha(data.light_brightness_a)

    async def _async_set(self, laser_brightness: int) -> None:
        try:
            await self.coordinator.client.set_light_brightness(laser_brightness)
        except XToolS1ConnectionError as err:
            raise HomeAssistantError(
                f"Failed to set xTool S1 light brightness: {err}"
            ) from err
        # Push the optimistic state through the coordinator so subscribers
        # (entities) get notified immediately.
        self.coordinator.async_set_updated_data(self.coordinator.client.state)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the fill light on, optionally at a given brightness."""
        if ATTR_BRIGHTNESS in kwargs:
            laser_brightness = _ha_to_laser(kwargs[ATTR_BRIGHTNESS])
            # Treat brightness=0 as turn-off.
            if laser_brightness == 0:
                await self._async_set(0)
                return
        else:
            data = self.coordinator.data
            current = data.light_brightness_a if data else None
            laser_brightness = current if current and current > 0 else 100
        await self._async_set(laser_brightness)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the fill light off."""
        await self._async_set(0)
