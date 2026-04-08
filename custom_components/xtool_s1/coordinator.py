"""DataUpdateCoordinator for the xTool S1 (push + watchdog poll)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import XToolS1Client, XToolS1ConnectionError, XToolS1State
from .const import DOMAIN, UPDATE_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class XToolS1RuntimeData:
    """Runtime data attached to the config entry."""

    coordinator: XToolS1Coordinator
    client: XToolS1Client


type XToolS1ConfigEntry = ConfigEntry[XToolS1RuntimeData]


class XToolS1Coordinator(DataUpdateCoordinator[XToolS1State]):
    """Hybrid push + poll coordinator.

    The S1 actively pushes state changes over the WebSocket. Whenever a
    new state arrives, :meth:`_handle_push` calls
    :meth:`async_set_updated_data` so HA entities update in real time.

    The :attr:`update_interval` poll only acts as a watchdog: if the
    socket has died, :meth:`_async_update_data` reconnects and asks the
    device for a fresh status snapshot. On a healthy connection it just
    sends a cheap ``M303`` ping to refresh position.
    """

    config_entry: XToolS1ConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        entry: XToolS1ConfigEntry,
        client: XToolS1Client,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.unique_id or entry.entry_id}",
            update_interval=timedelta(seconds=UPDATE_INTERVAL_SECONDS),
        )
        self.client = client
        self._unsub_push = client.on_state(self._handle_push)

    @callback
    def _handle_push(self, state: XToolS1State) -> None:
        """Receive a pushed state from the WebSocket listener."""
        self.async_set_updated_data(state)

    async def _async_update_data(self) -> XToolS1State:
        """Watchdog tick: ensure the connection is live and refresh.

        On a fresh connection we use :meth:`XToolS1Client.probe_initial_state`
        so the call only returns once the device has actually replied with
        a populated M2003 snapshot. Subsequent ticks just send a cheap
        ``M303`` ping and return whatever the listener has buffered.
        """
        if not self.client.connected:
            try:
                return await self.client.probe_initial_state()
            except XToolS1ConnectionError as err:
                raise UpdateFailed(
                    f"Cannot reach xTool S1 at {self.client.host}: {err}"
                ) from err
        try:
            await self.client.ping()
        except XToolS1ConnectionError as err:
            raise UpdateFailed(
                f"Lost connection to xTool S1 at {self.client.host}: {err}"
            ) from err
        return self.client.state

    async def async_shutdown(self) -> None:
        """Cancel the push subscription and close the WebSocket."""
        self._unsub_push()
        await self.client.disconnect()
        await super().async_shutdown()
