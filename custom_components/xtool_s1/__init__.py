"""The xTool S1 Laser integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import const
from .api import XToolS1Client
from .coordinator import XToolS1ConfigEntry, XToolS1Coordinator, XToolS1RuntimeData

PLATFORMS: tuple[Platform, ...] = (
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.LIGHT,
    Platform.BUTTON,
)


async def async_setup_entry(hass: HomeAssistant, entry: XToolS1ConfigEntry) -> bool:
    """Set up xTool S1 from a config entry."""
    session = async_get_clientsession(hass)
    # Read WS_PORT and HTTP_PORT through the module so tests can
    # monkey-patch them to point at a local fake server.
    client = XToolS1Client(
        entry.data[CONF_HOST],
        session,
        port=const.WS_PORT,
        http_port=const.HTTP_PORT,
    )
    coordinator = XToolS1Coordinator(hass, entry, client)

    # test-before-setup: failure here puts the entry into "retry".
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = XToolS1RuntimeData(coordinator=coordinator, client=client)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: XToolS1ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await entry.runtime_data.coordinator.async_shutdown()
    return unload_ok


async def _async_update_listener(
    hass: HomeAssistant, entry: XToolS1ConfigEntry
) -> None:
    """Reload the entry when its data changes (e.g. host updated)."""
    await hass.config_entries.async_reload(entry.entry_id)
