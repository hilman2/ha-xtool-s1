"""DataUpdateCoordinator for the xTool S1 (push + watchdog poll).

The S1 has two read paths and the integration uses both:

* The **WebSocket** on port 8081 streams state-change push frames
  (M2003 snapshots, M222/M810/M340/... deltas). The XCS app can kick
  this socket while it is itself talking to the laser, so we have to
  handle drops and reconnects gracefully.
* The **HTTP** ``GET /system?action=mac`` endpoint on port 8080 is the
  cheapest health check the device offers — it survives whatever the
  app does to the WebSocket. We fall back to it as a heartbeat when the
  WS reconnect fails repeatedly so the integration stays *online* (and
  HTTP-side writes still work) instead of going *unavailable*.

The reconnect ladder is intentionally generous on the upper end — once
the app has kicked us a few times in quick succession we wait several
minutes before trying again instead of fighting the app for the socket.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import XToolS1Client, XToolS1ConnectionError, XToolS1State
from .const import DOMAIN, RECONNECT_BACKOFF_SECONDS, UPDATE_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)

# A WebSocket session shorter than this is treated as "kicked by the
# app" — the next reconnect goes one rung up the backoff ladder
# instead of resetting to the bottom.
_KICK_DETECTION_SECONDS = 10.0


@dataclass(slots=True)
class XToolS1RuntimeData:
    """Runtime data attached to the config entry."""

    coordinator: XToolS1Coordinator
    client: XToolS1Client


type XToolS1ConfigEntry = ConfigEntry[XToolS1RuntimeData]


class XToolS1Coordinator(DataUpdateCoordinator[XToolS1State]):
    """Hybrid push + poll coordinator with HTTP fallback heartbeat."""

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
        self._connected_at: float | None = None
        self._backoff_index = 0
        self._next_reconnect_at: float | None = None

    @callback
    def _handle_push(self, state: XToolS1State) -> None:
        """Receive a pushed state from the WebSocket listener."""
        self.async_set_updated_data(state)

    # -- backoff bookkeeping --------------------------------------------

    def _note_connected(self) -> None:
        self._connected_at = time.monotonic()
        self._backoff_index = 0
        self._next_reconnect_at = None

    def _note_disconnected(self, *, kicked: bool) -> None:
        """Schedule the next reconnect according to the backoff ladder.

        ``kicked=True`` means the disconnect happened suspiciously
        quickly after the last successful connect, suggesting the XCS
        app is actively claiming the socket. We climb the ladder
        faster in that case.
        """
        if kicked and self._backoff_index < len(RECONNECT_BACKOFF_SECONDS) - 1:
            self._backoff_index += 1
        elif not kicked:
            # Soft drop (network blip) — slow climb.
            self._backoff_index = min(
                self._backoff_index + 1, len(RECONNECT_BACKOFF_SECONDS) - 1
            )
        delay = RECONNECT_BACKOFF_SECONDS[self._backoff_index]
        self._next_reconnect_at = time.monotonic() + delay
        _LOGGER.debug(
            "S1 %s disconnect (kicked=%s); next reconnect in %.0fs",
            self.client.host,
            kicked,
            delay,
        )

    def _is_in_backoff(self) -> bool:
        return (
            self._next_reconnect_at is not None
            and time.monotonic() < self._next_reconnect_at
        )

    # -- watchdog tick --------------------------------------------------

    async def _async_update_data(self) -> XToolS1State:
        """Watchdog tick: bring the WebSocket up, ping, fall back to HTTP."""
        if not self.client.connected:
            if self._is_in_backoff():
                # Skip the WebSocket reconnect this round but still
                # confirm the device is reachable via HTTP. As long as
                # ``/system?action=mac`` answers we keep the entry
                # marked as available — entities will rely on the
                # cached state until the WS comes back.
                if await self._http_heartbeat():
                    return self.client.state
                raise UpdateFailed(f"xTool S1 at {self.client.host} is not reachable")

            try:
                state = await self.client.probe_initial_state()
            except XToolS1ConnectionError as err:
                # Detect a fast kick: if we never even completed the
                # probe, treat it as a soft drop and climb gently.
                self._note_disconnected(kicked=False)
                raise UpdateFailed(
                    f"Cannot reach xTool S1 at {self.client.host}: {err}"
                ) from err
            self._note_connected()
            return state

        # We were connected at the top of the call. If the listener has
        # since dropped (e.g. the app just kicked us), record the kick
        # before raising so the next watchdog tick honours the backoff.
        try:
            await self.client.ping()
        except XToolS1ConnectionError as err:
            kicked = (
                self._connected_at is not None
                and time.monotonic() - self._connected_at < _KICK_DETECTION_SECONDS
            )
            self._note_disconnected(kicked=kicked)
            raise UpdateFailed(
                f"Lost connection to xTool S1 at {self.client.host}: {err}"
            ) from err
        return self.client.state

    async def _http_heartbeat(self) -> bool:
        """Probe the HTTP /system?action=mac endpoint as a liveness check."""
        try:
            mac = await self.client.fetch_mac_http()
        except XToolS1ConnectionError:
            return False
        return mac is not None

    async def async_shutdown(self) -> None:
        """Cancel the push subscription and close the WebSocket."""
        self._unsub_push()
        await self.client.disconnect()
        await super().async_shutdown()
