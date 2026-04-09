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
    BINARY_SENSOR_JOB_ARMED,
    BINARY_SENSOR_LAST_JOB_ABORTED,
    BINARY_SENSOR_PAUSED,
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


def _paused_is_on(state: XToolS1State) -> bool:
    """The job is paused (M222=S15)."""
    return state.work_state_raw == "S15"


def _last_job_aborted_is_on(state: XToolS1State) -> bool:
    """Sticky abnormal-finish marker.

    The S1 leaves M22 at S1 after a stopped job; a normal finish
    resets M22 to S0. So when we are back at idle (S3) and M22 is
    still S1, the previous job ended via Stop instead of finishing.
    """
    return state.work_state_raw == "S3" and state.m22_state == "S1"


def _job_armed_is_on(state: XToolS1State) -> bool:
    """Job loaded, waiting for the physical Start button.

    Verified flow:
      * the user clicks Start in XCS → first M323 OK push
      * the laser blocks until the user presses the device's button
      * a second M323 OK arrives, then M222 S13 (Starting)

    So between the first and second M323 OK we sit at S3 with the
    head positioned away from the parking spot. That's exactly the
    moment we want to surface — perfect for an HA notification
    "press the start button on the laser".
    """
    return (
        state.work_state_raw == "S3"
        and state.m323_ack_count == 1
        and state.pos_x is not None
        and state.pos_y is not None
        # Park position is around X≈0, Y≈99.8 — anything noticeably
        # different means the head has been moved by the job preload.
        and not (abs(state.pos_x) < 5.0 and abs(state.pos_y - 99.8) < 5.0)
    )


BINARY_SENSOR_DESCRIPTIONS: tuple[XToolS1BinarySensorDescription, ...] = (
    XToolS1BinarySensorDescription(
        key=BINARY_SENSOR_RUNNING,
        translation_key=BINARY_SENSOR_RUNNING,
        device_class=BinarySensorDeviceClass.RUNNING,
        is_on_fn=_running_is_on,
    ),
    XToolS1BinarySensorDescription(
        key=BINARY_SENSOR_PAUSED,
        translation_key=BINARY_SENSOR_PAUSED,
        is_on_fn=_paused_is_on,
    ),
    XToolS1BinarySensorDescription(
        key=BINARY_SENSOR_LAST_JOB_ABORTED,
        translation_key=BINARY_SENSOR_LAST_JOB_ABORTED,
        device_class=BinarySensorDeviceClass.PROBLEM,
        is_on_fn=_last_job_aborted_is_on,
    ),
    XToolS1BinarySensorDescription(
        key=BINARY_SENSOR_JOB_ARMED,
        translation_key=BINARY_SENSOR_JOB_ARMED,
        is_on_fn=_job_armed_is_on,
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
            return self.coordinator.data is not None
        return super().available

    @property
    def is_on(self) -> bool | None:
        """Return the boolean value extracted from the latest state."""
        if self.coordinator.data is None:
            return None
        return self.entity_description.is_on_fn(self.coordinator.data)
