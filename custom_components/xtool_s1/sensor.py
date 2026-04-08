"""Sensor platform for the xTool S1 integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import XToolS1State
from .const import (
    SENSOR_FIRMWARE_AUX_1,
    SENSOR_FIRMWARE_AUX_2,
    SENSOR_FIRMWARE_TOOL,
    SENSOR_FIRMWARE_VERSION,
    SENSOR_JOB_FILE,
    SENSOR_LIGHT_BRIGHTNESS,
    SENSOR_POSITION_X,
    SENSOR_POSITION_Y,
    SENSOR_PROBE_Z,
    SENSOR_SERIAL_NUMBER,
    SENSOR_STATUS,
    SENSOR_TOOL_TYPE,
    STATUS_OPTIONS,
    STATUS_UNKNOWN,
    WORK_STATE_MAP,
)
from .coordinator import XToolS1ConfigEntry
from .entity import XToolS1Entity

PARALLEL_UPDATES = 0  # coordinator-driven, no parallel writes from entities


@dataclass(frozen=True, kw_only=True)
class XToolS1SensorDescription(SensorEntityDescription):
    """Describes an xTool S1 sensor."""

    value_fn: Callable[[XToolS1State], Any]


def _status_value(state: XToolS1State) -> str:
    if state.work_state_raw is None:
        return STATUS_UNKNOWN
    return WORK_STATE_MAP.get(state.work_state_raw, STATUS_UNKNOWN)


SENSOR_DESCRIPTIONS: tuple[XToolS1SensorDescription, ...] = (
    XToolS1SensorDescription(
        key=SENSOR_STATUS,
        translation_key=SENSOR_STATUS,
        device_class=SensorDeviceClass.ENUM,
        options=list(STATUS_OPTIONS),
        value_fn=_status_value,
    ),
    XToolS1SensorDescription(
        key=SENSOR_FIRMWARE_VERSION,
        translation_key=SENSOR_FIRMWARE_VERSION,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.firmware_version,
    ),
    XToolS1SensorDescription(
        key=SENSOR_FIRMWARE_AUX_1,
        translation_key=SENSOR_FIRMWARE_AUX_1,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.firmware_aux_1,
    ),
    XToolS1SensorDescription(
        key=SENSOR_FIRMWARE_AUX_2,
        translation_key=SENSOR_FIRMWARE_AUX_2,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.firmware_aux_2,
    ),
    XToolS1SensorDescription(
        key=SENSOR_FIRMWARE_TOOL,
        translation_key=SENSOR_FIRMWARE_TOOL,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.firmware_tool,
    ),
    XToolS1SensorDescription(
        key=SENSOR_SERIAL_NUMBER,
        translation_key=SENSOR_SERIAL_NUMBER,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.serial_number,
    ),
    XToolS1SensorDescription(
        key=SENSOR_TOOL_TYPE,
        translation_key=SENSOR_TOOL_TYPE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.tool_type,
    ),
    XToolS1SensorDescription(
        key=SENSOR_JOB_FILE,
        translation_key=SENSOR_JOB_FILE,
        value_fn=lambda s: s.job_file,
    ),
    XToolS1SensorDescription(
        key=SENSOR_POSITION_X,
        translation_key=SENSOR_POSITION_X,
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda s: s.pos_x,
    ),
    XToolS1SensorDescription(
        key=SENSOR_POSITION_Y,
        translation_key=SENSOR_POSITION_Y,
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=lambda s: s.pos_y,
    ),
    XToolS1SensorDescription(
        key=SENSOR_PROBE_Z,
        translation_key=SENSOR_PROBE_Z,
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=3,
        value_fn=lambda s: s.probe_z,
    ),
    XToolS1SensorDescription(
        key=SENSOR_LIGHT_BRIGHTNESS,
        translation_key=SENSOR_LIGHT_BRIGHTNESS,
        native_unit_of_measurement="%",
        state_class=SensorStateClass.MEASUREMENT,
        # Both M13 channels carry the same value via the app — read one.
        value_fn=lambda s: s.light_brightness_a,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: XToolS1ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the xTool S1 sensors from a config entry."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities(
        XToolS1Sensor(coordinator, description) for description in SENSOR_DESCRIPTIONS
    )


class XToolS1Sensor(XToolS1Entity, SensorEntity):
    """A single sensor backed by an XToolS1State field."""

    entity_description: XToolS1SensorDescription

    def __init__(
        self,
        coordinator,
        description: XToolS1SensorDescription,
    ) -> None:
        self._attr_translation_key = description.translation_key or description.key
        super().__init__(coordinator)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the value extracted from the latest state snapshot."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
