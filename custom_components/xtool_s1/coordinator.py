"""DataUpdateCoordinator for the xTool S1 with HTTP/WS coexist support.

The S1 has two read paths and the integration uses both:

* The **WebSocket** on port 8081 streams state-change push frames in
  real time. The XCS desktop app sends ``M303`` (position read) once
  per second whenever it's open, and the resulting fan-out to all
  connected clients pushes our foreign WS connection over a fairness
  limit in the firmware — we get kicked every few seconds. See
  ``docs/PROTOCOL.md`` §5.5h for the full analysis.
* The **HTTP** gateway on port 8080 (``GET /system?action=mac``) is
  the cheapest health check the device offers. It survives whatever
  the app does to the WebSocket and is used as the primary heartbeat
  whenever the WS turns out to be unreliable.

The coordinator runs in one of three modes:

* **Normal** — WS is healthy. Push frames update state in real time.
* **Coexist** — at least :data:`COEXIST_KICK_LIMIT` disconnects in
  the last :data:`COEXIST_KICK_WINDOW` seconds. The XCS desktop app
  is most likely open. We stop hammering the WS and instead poll
  the HTTP heartbeat. State sensors keep showing their last known
  cached values; HTTP-side entities (light, buttons) stay fully
  operational. We try a single WS reconnect every
  :data:`COEXIST_HEARTBEAT_INTERVAL`-ish — if it survives long enough
  we drop back to Normal.
* **Offline** — HTTP heartbeat has been failing for too long. Mark
  everything unavailable.

Periodic side jobs:

* Every :data:`STATS_POLL_INTERVAL` seconds we send ``M2008`` to
  refresh the lifetime counters (working time, sessions, standby,
  per-tool runtime). They aren't pushed spontaneously, only on
  request.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from datetime import timedelta
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import XToolS1Client, XToolS1ConnectionError, XToolS1State
from .const import (
    COEXIST_KICK_LIMIT,
    COEXIST_KICK_WINDOW,
    COEXIST_RECOVERY_AFTER,
    DOMAIN,
    RECONNECT_BACKOFF_SECONDS,
    STATS_POLL_INTERVAL,
    UPDATE_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

# A WebSocket session shorter than this is treated as "kicked by the app".
_KICK_DETECTION_SECONDS = 10.0

# How long the HTTP heartbeat may fail before we declare the device offline.
_OFFLINE_AFTER_HTTP_FAILS = 60.0

#: Coordinator mode constants.
MODE_NORMAL = "normal"
MODE_COEXIST = "coexist"
MODE_OFFLINE = "offline"


@dataclass(slots=True)
class XToolS1RuntimeData:
    """Runtime data attached to the config entry."""

    coordinator: XToolS1Coordinator
    client: XToolS1Client


type XToolS1ConfigEntry = ConfigEntry[XToolS1RuntimeData]


class XToolS1Coordinator(DataUpdateCoordinator[XToolS1State]):
    """Three-mode coordinator: WS push, HTTP coexist, offline."""

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
        self._mode: str = MODE_NORMAL
        self._mode_changed_at: float = time.monotonic()
        self._connected_at: float | None = None
        self._backoff_index = 0
        self._next_reconnect_at: float | None = None
        self._kick_log: deque[float] = deque(maxlen=COEXIST_KICK_LIMIT * 2)
        self._http_ok_at: float | None = None
        self._http_failing_since: float | None = None
        self._last_stats_poll: float = 0.0

    # -- public properties used by entities -----------------------------

    @property
    def mode(self) -> str:
        """Return the current coordinator mode (normal/coexist/offline)."""
        return self._mode

    @property
    def http_reachable(self) -> bool:
        """Return True if the last HTTP heartbeat was successful.

        HTTP-only entities (light, buttons) use this to gate their
        ``available`` property — they don't care whether the WebSocket
        is up or not.
        """
        return self._mode != MODE_OFFLINE

    @callback
    def _handle_push(self, state: XToolS1State) -> None:
        """Receive a pushed state from the WebSocket listener."""
        self.async_set_updated_data(state)

    # -- mode transitions -----------------------------------------------

    def _enter_mode(self, mode: str) -> None:
        if mode == self._mode:
            return
        _LOGGER.debug(
            "S1 %s coordinator mode: %s -> %s", self.client.host, self._mode, mode
        )
        self._mode = mode
        self._mode_changed_at = time.monotonic()

    # -- backoff bookkeeping --------------------------------------------

    def _note_connected(self) -> None:
        self._connected_at = time.monotonic()
        self._backoff_index = 0
        self._next_reconnect_at = None

    def _note_disconnected(self, *, kicked: bool) -> None:
        """Schedule the next reconnect according to the backoff ladder."""
        if kicked:
            self._kick_log.append(time.monotonic())
            if self._backoff_index < len(RECONNECT_BACKOFF_SECONDS) - 1:
                self._backoff_index += 1
        else:
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
        # If we've been kicked too many times in a short window, jump
        # straight to coexist mode.
        if self._is_in_kick_storm():
            self._enter_mode(MODE_COEXIST)

    def _is_in_kick_storm(self) -> bool:
        """Return True if recent kicks suggest the XCS app is open."""
        if len(self._kick_log) < COEXIST_KICK_LIMIT:
            return False
        recent_kicks = [
            t for t in self._kick_log if time.monotonic() - t < COEXIST_KICK_WINDOW
        ]
        return len(recent_kicks) >= COEXIST_KICK_LIMIT

    def _is_in_backoff(self) -> bool:
        return (
            self._next_reconnect_at is not None
            and time.monotonic() < self._next_reconnect_at
        )

    # -- watchdog tick --------------------------------------------------

    async def _async_update_data(self) -> XToolS1State:
        """Watchdog tick — picks the right path for the current mode."""
        try:
            if self._mode == MODE_COEXIST:
                state = await self._coexist_tick()
            elif self._mode == MODE_OFFLINE:
                state = await self._offline_tick()
            else:
                state = await self._normal_tick()
        finally:
            await self._maybe_poll_stats()
        return state

    async def _normal_tick(self) -> XToolS1State:  # noqa: PLR0911
        """Normal mode: bring up the WS if needed, ping otherwise."""
        if not self.client.connected:
            if self._is_in_backoff():
                if await self._http_heartbeat():
                    return self.client.state
                return self._power_off_snapshot()

            try:
                state = await self.client.probe_initial_state()
            except XToolS1ConnectionError:
                self._note_disconnected(kicked=False)
                if self._mode == MODE_COEXIST:
                    # The kick-storm check switched us mid-call.
                    return await self._coexist_tick()
                if await self._http_heartbeat():
                    return self.client.state
                return self._power_off_snapshot()
            self._note_connected()
            self._mark_http_ok()
            return state

        try:
            await self.client.ping()
        except XToolS1ConnectionError:
            kicked = (
                self._connected_at is not None
                and time.monotonic() - self._connected_at < _KICK_DETECTION_SECONDS
            )
            self._note_disconnected(kicked=kicked)
            if self._mode == MODE_COEXIST:
                return await self._coexist_tick()
            return self._power_off_snapshot()
        self._mark_http_ok()  # ping success implies the device is up
        return self.client.state

    async def _coexist_tick(self) -> XToolS1State:
        """Coexist mode: HTTP heartbeat only, plus a periodic WS attempt.

        We don't try to keep the WebSocket open — that would just keep
        getting kicked. Instead we run on the HTTP heartbeat and try a
        single WS reconnect every COEXIST_RECOVERY_AFTER seconds; if
        the WS survives long enough to read M2003, we drop back to
        Normal mode.
        """
        # Make sure any leftover WS state is cleaned up.
        if self.client.connected:
            await self.client.disconnect()

        if not await self._http_heartbeat():
            return self._power_off_snapshot()

        since_mode = time.monotonic() - self._mode_changed_at
        if since_mode > COEXIST_RECOVERY_AFTER:
            # Optimistic recovery attempt — try to grab a fresh state
            # via a short-lived WS connect.
            try:
                fresh = await self.client.probe_initial_state(timeout=2.0)
            except XToolS1ConnectionError:
                # Still being kicked. Stay in coexist, reset the timer.
                self._mode_changed_at = time.monotonic()
            else:
                self._enter_mode(MODE_NORMAL)
                self._note_connected()
                return fresh

        # Cached state from the last successful read.
        return self.client.state

    async def _offline_tick(self) -> XToolS1State:
        """Offline mode: keep probing the HTTP heartbeat to recover."""
        if await self._http_heartbeat():
            self._enter_mode(MODE_NORMAL)
            return self.client.state
        # Device is off — normal state, no error.
        return self._power_off_snapshot()

    async def _maybe_poll_stats(self) -> None:
        """Send ``M2008`` periodically to refresh lifetime counters."""
        if self._mode == MODE_OFFLINE:
            return
        now = time.monotonic()
        if now - self._last_stats_poll < STATS_POLL_INTERVAL:
            return
        try:
            await self.client.request_stats()
        except XToolS1ConnectionError:
            return
        self._last_stats_poll = now

    # -- HTTP heartbeat -------------------------------------------------

    async def _http_heartbeat(self) -> bool:
        """Probe ``GET /system?action=mac`` as a liveness check."""
        try:
            mac = await self.client.fetch_mac_http()
        except XToolS1ConnectionError:
            return self._mark_http_fail()
        if mac is None:
            return self._mark_http_fail()
        self._mark_http_ok()
        return True

    def _mark_http_ok(self) -> None:
        self._http_ok_at = time.monotonic()
        self._http_failing_since = None
        if self._mode == MODE_OFFLINE:
            self._enter_mode(MODE_NORMAL)

    def _mark_http_fail(self) -> bool:
        if self._http_failing_since is None:
            self._http_failing_since = time.monotonic()
        return False

    def _power_off_snapshot(self) -> XToolS1State:
        """Return a state snapshot suitable for a powered-off device.

        Preserves info fields (firmware, serial, counters, tool) from
        the last known state but zeros all dynamic/operational fields
        so HA shows a clean "device is off" picture without going
        unavailable.
        """
        if (
            self._http_failing_since is not None
            and time.monotonic() - self._http_failing_since >= _OFFLINE_AFTER_HTTP_FAILS
        ):
            self._enter_mode(MODE_OFFLINE)

        last = self.client.state
        return replace(
            last,
            connected=False,
            work_state_raw=None,
            job_file=None,
            pos_x=None,
            pos_y=None,
            pos_z=None,
            pos_u=None,
            probe_z=last.probe_z,  # keep last reading
            light_brightness_a=0,
            light_brightness_b=0,
            light_active=False,
            alarm_raw=None,
            alarm_present=False,
            m22_state=None,
            m323_ack_count=0,
        )

    async def async_shutdown(self) -> None:
        """Cancel the push subscription and close the WebSocket."""
        self._unsub_push()
        await self.client.disconnect()
        await super().async_shutdown()
