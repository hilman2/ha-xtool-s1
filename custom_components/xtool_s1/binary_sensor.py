"""Binary sensor platform for the xTool S1 integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import XToolS1State
from .const import (
    BINARY_SENSOR_ALARM,
    BINARY_SENSOR_CONNECTION,
    BINARY_SENSOR_RUNNING,
    RUNNING_WORK_STATES,
)
from .coordinator import XToolS1ConfigEntry
from .entity import XToolS1Entity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class XToolS1BinarySensorDescription(BinarySensorEntityDescription):
    """Describes an xTool S1 binary sensor."""

    is_on_fn: Callable[[XToolS1State], bool]


def _running_is_on(state: XToolS1State) -> bool:
    return state.work_state_raw in RUNNING_WORK_STATES


def _alarm_is_on(state: XToolS1State) -> bool:
    return state.alarm_present


def _connection_is_on(state: XToolS1State) -> bool:
    return state.connected


BINARY_SENSOR_DESCRIPTIONS: tuple[XToolS1BinarySensorDescription, ...] = (
    XToolS1BinarySensorDescription(
        key=BINARY_SENSOR_RUNNING,
        translation_key=BINARY_SENSOR_RUNNING,
        device_class=BinarySensorDeviceClass.RUNNING,
        is_on_fn=_running_is_on,
    ),
    XToolS1BinarySensorDescription(
        key=BINARY_SENSOR_ALARM,
        translation_key=BINARY_SENSOR_ALARM,
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=_alarm_is_on,
    ),
    XToolS1BinarySensorDescription(
        key=BINARY_SENSOR_CONNECTION,
        translation_key=BINARY_SENSOR_CONNECTION,
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        is_on_fn=_connection_is_on,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XToolS1ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the xTool S1 binary sensors from a config entry."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        XToolS1BinarySensor(coordinator, description)
        for description in BINARY_SENSOR_DESCRIPTIONS
    )


class XToolS1BinarySensor(XToolS1Entity, BinarySensorEntity):
    """A single binary sensor backed by an XToolS1State field."""

    entity_description: XToolS1BinarySensorDescription

    def __init__(
        self,
        coordinator,
        description: XToolS1BinarySensorDescription,
    ) -> None:
        self._attr_translation_key = description.translation_key or description.key
        super().__init__(coordinator)
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Connection sensor must stay available so users can see ``off``."""
        if self.entity_description.key == BINARY_SENSOR_CONNECTION:
            # Coordinator may be in a failed state but we still want to
            # surface the connectivity sensor as "off" — not "unavailable".
            return self.coordinator.data is not None
        return super().available

    @property
    def is_on(self) -> bool | None:
        """Return the boolean value extracted from the latest state."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.is_on_fn(self.coordinator.data)
