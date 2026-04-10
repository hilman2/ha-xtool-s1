"""Diagnostics support for the xTool S1 integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant

from .const import RAW_PROTOCOL_RING_BUFFER_SIZE
from .coordinator import XToolS1ConfigEntry

TO_REDACT = {CONF_HOST, "serial_number", "host"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: XToolS1ConfigEntry,
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    state = coordinator.data
    state_dict = asdict(state) if state is not None else None

    return {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "unique_id": entry.unique_id,
        },
        "client": {
            "host": "**REDACTED**",
            "connected": runtime.client.connected,
        },
        "coordinator": {
            "mode": coordinator.mode,
            "last_update_success": coordinator.last_update_success,
            "update_interval_seconds": (
                coordinator.update_interval.total_seconds()
                if coordinator.update_interval
                else None
            ),
        },
        "state": async_redact_data(state_dict, TO_REDACT) if state_dict else None,
        "raw_protocol_ring_buffer_size": RAW_PROTOCOL_RING_BUFFER_SIZE,
        "raw_protocol_ring_buffer": runtime.client.raw_protocol_frames,
        "last_debug_export_at": coordinator.last_debug_export_at,
        "last_debug_export_url": coordinator.last_debug_export_url,
    }
