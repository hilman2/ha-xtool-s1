"""Base entity for the xTool S1 integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER, MODEL
from .coordinator import XToolS1Coordinator


class XToolS1Entity(CoordinatorEntity[XToolS1Coordinator]):
    """Common base for all xTool S1 entities.

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
        data = coordinator.data
        # Prefer the model name reported by the device (M100 in M2003)
        # over the hard-coded fallback.
        model = (data.model_name if data and data.model_name else None) or MODEL
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, unique_root)},
            manufacturer=MANUFACTURER,
            model=model,
            name="xTool S1",
            sw_version=data.firmware_version if data else None,
            serial_number=data.serial_number if data else None,
            configuration_url=f"http://{coordinator.config_entry.data[CONF_HOST]}",
        )

    @property
    def available(self) -> bool:
        """Return True if the coordinator has data and the WebSocket is up."""
        if not super().available:
            return False
        data = self.coordinator.data
        return data is not None and data.connected
