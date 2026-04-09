"""Base entity classes for the xTool S1 integration.

Two base classes:

* :class:`XToolS1Entity` — for read-only entities backed by WebSocket
  state. Available iff the coordinator has data AND is not in offline
  mode. In coexist mode, sensors fall back to their last known cached
  state with a ``stale`` attribute so HA automations don't see them
  as unavailable.

* :class:`XToolS1HttpEntity` — for HTTP-only entities (light, buttons).
  These never depend on the WebSocket — their availability is gated
  purely by the HTTP heartbeat. The fill light, the stop button, and
  the pause/resume buttons all stay fully usable even when the XCS
  desktop app is hammering the WebSocket.
"""

from __future__ import annotations

from typing import Any

from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import MODE_COEXIST, XToolS1Coordinator


def _build_device_info(coordinator: XToolS1Coordinator) -> DeviceInfo:
    unique_root = (
        coordinator.config_entry.unique_id or coordinator.config_entry.entry_id
    )
    data = coordinator.data
    model = (data.model_name if data and data.model_name else None) or MODEL
    return DeviceInfo(
        identifiers={(DOMAIN, unique_root)},
        manufacturer=MANUFACTURER,
        model=model,
        name="xTool S1",
        sw_version=data.firmware_version if data else None,
        serial_number=data.serial_number if data else None,
        configuration_url=f"http://{coordinator.config_entry.data[CONF_HOST]}",
    )


class XToolS1Entity(CoordinatorEntity[XToolS1Coordinator]):
    """Common base for read-only WebSocket-backed entities.

    Subclasses MUST set ``_attr_translation_key`` as a class attribute
    so the unique-id and the strings.json lookup line up.
    """

    _attr_has_entity_name = True
    _attr_translation_key: str

    def __init__(self, coordinator: XToolS1Coordinator) -> None:
        super().__init__(coordinator)
        unique_root = (
            coordinator.config_entry.unique_id or coordinator.config_entry.entry_id
        )
        self._attr_unique_id = f"{unique_root}_{self._attr_translation_key}"
        self._attr_device_info = _build_device_info(coordinator)

    @property
    def available(self) -> bool:
        """Always available once the first data snapshot has arrived.

        The laser is normally powered off most of the time — that is
        NOT an error. Dynamic sensors return their "off" values via
        the coordinator's power-off snapshot; info sensors keep their
        last known value. The ``connection`` binary sensor is the
        indicator for "device is on".
        """
        return self.coordinator.data is not None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Mark cached values from coexist mode as stale."""
        if self.coordinator.mode == MODE_COEXIST:
            return {"stale": True}
        return {}


class XToolS1HttpEntity(CoordinatorEntity[XToolS1Coordinator]):
    """Base for entities whose actions go over HTTP only.

    These do not depend on the WebSocket and stay available as long
    as the device's HTTP gateway answers — that means they keep
    working even while the XCS desktop app is kicking the foreign
    WebSocket every few seconds.
    """

    _attr_has_entity_name = True
    _attr_translation_key: str

    def __init__(self, coordinator: XToolS1Coordinator) -> None:
        super().__init__(coordinator)
        unique_root = (
            coordinator.config_entry.unique_id or coordinator.config_entry.entry_id
        )
        self._attr_unique_id = f"{unique_root}_{self._attr_translation_key}"
        self._attr_device_info = _build_device_info(coordinator)

    @property
    def available(self) -> bool:
        """Always available once the first data snapshot has arrived.

        HTTP-only entities (light, buttons) stay available even when
        the device is off — pressing a button while the laser is off
        simply fails silently rather than hiding the control entirely.
        """
        return self.coordinator.data is not None
