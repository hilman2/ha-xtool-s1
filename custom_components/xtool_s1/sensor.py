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
from homeassistant.const import EntityCategory, UnitOfLength, UnitOfPower, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import XToolS1State
from .const import (
    OUTCOME_ABORTED,
    OUTCOME_COMPLETED,
    OUTCOME_IDLE,
    OUTCOME_OPTIONS,
    OUTCOME_PAUSED,
    OUTCOME_RUNNING,
    SENSOR_FIRMWARE_AUX_1,
    SENSOR_FIRMWARE_AUX_2,
    SENSOR_FIRMWARE_TOOL,
    SENSOR_FIRMWARE_VERSION,
    SENSOR_JOB_FILE,
    SENSOR_LAST_JOB_OUTCOME,
    SENSOR_LIGHT_BRIGHTNESS,
    SENSOR_POSITION_X,
    SENSOR_POSITION_Y,
    SENSOR_POSITION_Z,
    SENSOR_PROBE_Z,
    SENSOR_SERIAL_NUMBER,
    SENSOR_SESSION_COUNT,
    SENSOR_STANDBY_TIME,
    SENSOR_STATUS,
    SENSOR_TOOL_CAPABILITIES,
    SENSOR_TOOL_FIRMWARE,
    SENSOR_TOOL_NAME,
    SENSOR_TOOL_OFFSET_X,
    SENSOR_TOOL_OFFSET_Y,
    SENSOR_TOOL_POWER,
    SENSOR_TOOL_RUNTIME,
    SENSOR_TOOL_TYPE,
    SENSOR_WORKING_TIME,
    STATUS_OPTIONS,
    STATUS_UNKNOWN,
    TOOL_FIRMWARE_NAMES,
    WORK_STATE_MAP,
)
from .coordinator import XToolS1ConfigEntry, XToolS1Coordinator
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


def _tool_name(state: XToolS1State) -> str | None:
    """Map the tool firmware fingerprint to a human-readable name."""
    fingerprint = state.firmware_aux_1
    if fingerprint is None:
        return None
    return TOOL_FIRMWARE_NAMES.get(fingerprint, "Unknown")


def _last_job_outcome(state: XToolS1State) -> str | None:  # noqa: PLR0911
    """Derive a single 'last job outcome' value from work state + M22.

    The mapping (verified on real hardware 2026-04-09):

    * S13/S14         → "running"
    * S15             → "paused"
    * S3 with M22 S1  → "aborted"  (sticky abnormal-finish marker)
    * S3 with M22 S0  → "completed" if a job had run, otherwise "idle"
    """
    raw = state.work_state_raw
    if raw is None:
        return None
    if raw in {"S13", "S14", "S19"}:
        return OUTCOME_RUNNING
    if raw == "S15":
        return OUTCOME_PAUSED
    if raw == "S3":
        if state.m22_state == "S1":
            return OUTCOME_ABORTED
        if state.session_count and state.session_count > 0:
            return OUTCOME_COMPLETED
        return OUTCOME_IDLE
    return OUTCOME_IDLE


def _seconds_to_hours(value: int | None) -> float | None:
    if value is None:
        return None
    return round(value / 3600.0, 2)


SENSOR_DESCRIPTIONS: tuple[XToolS1SensorDescription, ...] = (
    XToolS1SensorDescription(
        key=SENSOR_STATUS,
        translation_key=SENSOR_STATUS,
        device_class=SensorDeviceClass.ENUM,
        options=list(STATUS_OPTIONS),
        value_fn=_status_value,
    ),
    XToolS1SensorDescription(
        key=SENSOR_LAST_JOB_OUTCOME,
        translation_key=SENSOR_LAST_JOB_OUTCOME,
        device_class=SensorDeviceClass.ENUM,
        options=list(OUTCOME_OPTIONS),
        value_fn=_last_job_outcome,
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
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.tool_type,
    ),
    XToolS1SensorDescription(
        key=SENSOR_TOOL_NAME,
        translation_key=SENSOR_TOOL_NAME,
        value_fn=_tool_name,
    ),
    XToolS1SensorDescription(
        key=SENSOR_TOOL_POWER,
        translation_key=SENSOR_TOOL_POWER,
        device_class=SensorDeviceClass.POWER,
        native_unit_of_measurement=UnitOfPower.WATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.tool_power_w,
    ),
    XToolS1SensorDescription(
        key=SENSOR_TOOL_FIRMWARE,
        translation_key=SENSOR_TOOL_FIRMWARE,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.firmware_aux_1,
    ),
    XToolS1SensorDescription(
        key=SENSOR_TOOL_CAPABILITIES,
        translation_key=SENSOR_TOOL_CAPABILITIES,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.tool_capabilities_raw,
    ),
    XToolS1SensorDescription(
        key=SENSOR_TOOL_RUNTIME,
        translation_key=SENSOR_TOOL_RUNTIME,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        # M2008.D = accumulated working seconds of the current tool TYPE
        # (per-wattage counter, e.g. acc_40w_laserworktime in logs.txt).
        # Persistent across reboots, only ticks during active jobs.
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
        value_fn=lambda s: _seconds_to_hours(s.tool_runtime_seconds),
    ),
    XToolS1SensorDescription(
        key=SENSOR_TOOL_OFFSET_X,
        translation_key=SENSOR_TOOL_OFFSET_X,
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
        value_fn=lambda s: s.tool_offset_x,
    ),
    XToolS1SensorDescription(
        key=SENSOR_TOOL_OFFSET_Y,
        translation_key=SENSOR_TOOL_OFFSET_Y,
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
        value_fn=lambda s: s.tool_offset_y,
    ),
    XToolS1SensorDescription(
        key=SENSOR_WORKING_TIME,
        translation_key=SENSOR_WORKING_TIME,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        suggested_display_precision=2,
        value_fn=lambda s: _seconds_to_hours(s.working_seconds),
    ),
    XToolS1SensorDescription(
        key=SENSOR_STANDBY_TIME,
        translation_key=SENSOR_STANDBY_TIME,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
        value_fn=lambda s: _seconds_to_hours(s.standby_seconds),
    ),
    XToolS1SensorDescription(
        key=SENSOR_SESSION_COUNT,
        translation_key=SENSOR_SESSION_COUNT,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda s: s.session_count,
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
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
        value_fn=lambda s: s.pos_x,
    ),
    XToolS1SensorDescription(
        key=SENSOR_POSITION_Y,
        translation_key=SENSOR_POSITION_Y,
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
        value_fn=lambda s: s.pos_y,
    ),
    XToolS1SensorDescription(
        key=SENSOR_POSITION_Z,
        translation_key=SENSOR_POSITION_Z,
        device_class=SensorDeviceClass.DISTANCE,
        native_unit_of_measurement=UnitOfLength.MILLIMETERS,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        suggested_display_precision=2,
        value_fn=lambda s: s.pos_z,
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
        # Report 0 when the firmware has dimmed the light for standby
        # (M15 S0), even though M13 still carries the configured value.
        value_fn=lambda s: s.light_brightness_a if s.light_active else 0,
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
        coordinator: XToolS1Coordinator,
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
