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
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
import json
import logging
from pathlib import Path
import re
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import XToolS1Client, XToolS1ConnectionError, XToolS1State
from .const import (
    COEXIST_KICK_LIMIT,
    COEXIST_KICK_WINDOW,
    COEXIST_RECOVERY_AFTER,
    DEBUG_EXPORT_KEEP_FILES,
    DOMAIN,
    LOGFILE_MAX_SIZE,
    LOGFILE_POLL_INTERVAL,
    RAW_PROTOCOL_RING_BUFFER_SIZE,
    RECONNECT_BACKOFF_SECONDS,
    STATS_POLL_INTERVAL,
    UPDATE_INTERVAL_SECONDS,
)

_LOGGER = logging.getLogger(__name__)

# A WebSocket session shorter than this is treated as "kicked by the app".
_KICK_DETECTION_SECONDS = 10.0

# Regex to pull per-tool counters from the firmware's logs.txt.
_LOGFILE_COUNTER_RE = re.compile(r"acc_(\w+)_laserworktime:(\d+);", re.IGNORECASE)
_LOGFILE_WORKTIME_RE = re.compile(r"acc_worktime:(\d+);")
_LOGFILE_WORKCOUNT_RE = re.compile(r"acc_workcount:(\d+);")
_LOGFILE_SYSRUNTIME_RE = re.compile(r"acc_sys_runtime:(\d+);")


def _utcnow_iso() -> str:
    """Return a compact UTC timestamp for diagnostics payloads."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _write_debug_export_file(
    export_dir: Path,
    entry_id: str,
    filename: str,
    payload: dict[str, Any],
) -> None:
    """Persist a JSON debug export and keep only a small rolling window."""
    export_dir.mkdir(parents=True, exist_ok=True)
    path = export_dir / filename
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")

    stale_exports = sorted(
        export_dir.glob(f"xtool-s1-debug-{entry_id}-*.json"),
        key=lambda candidate: candidate.stat().st_mtime,
        reverse=True,
    )
    for stale in stale_exports[DEBUG_EXPORT_KEEP_FILES:]:
        stale.unlink(missing_ok=True)


def _parse_logfile_counters(tail: str) -> dict[str, Any]:
    """Extract the LAST occurrence of the counter block from a logs.txt tail.

    Returns a dict of state fields to update. Empty if no counters found.
    """
    out: dict[str, Any] = {}

    # Per-tool working times (e.g. acc_40w_laserworktime:3086;)
    tool_times: dict[str, int] = {}
    for match in _LOGFILE_COUNTER_RE.finditer(tail):
        wattage = match.group(1).lower()
        tool_times[wattage] = int(match.group(2))
    if tool_times:
        out["logfile_tool_times"] = tool_times

    # Global counters (take the LAST match)
    for match in _LOGFILE_WORKTIME_RE.finditer(tail):
        out["working_seconds"] = int(match.group(1))
    for match in _LOGFILE_WORKCOUNT_RE.finditer(tail):
        out["session_count"] = int(match.group(1))
    for match in _LOGFILE_SYSRUNTIME_RE.finditer(tail):
        out["standby_seconds"] = int(match.group(1))

    return out


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
        self._last_logfile_poll: float = 0.0
        self._last_debug_export_at: str | None = None
        self._last_debug_export_url: str | None = None

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

    @property
    def last_debug_export_at(self) -> str | None:
        """Return when the last JSON debug export was written."""
        return self._last_debug_export_at

    @property
    def last_debug_export_url(self) -> str | None:
        """Return the relative HA download URL for the last debug export."""
        return self._last_debug_export_url

    def build_debug_export_payload(
        self, *, generated_at: str | None = None
    ) -> dict[str, Any]:
        """Return a JSON-ready snapshot with current state and raw protocol frames."""
        state = self.data
        state_dict = asdict(state) if state is not None else None
        return {
            "generated_at": generated_at or _utcnow_iso(),
            "entry": {
                "entry_id": self.config_entry.entry_id,
                "title": self.config_entry.title,
                "unique_id": self.config_entry.unique_id,
                "host": self.config_entry.data.get(CONF_HOST),
            },
            "client": {
                "host": self.client.host,
                "connected": self.client.connected,
            },
            "coordinator": {
                "mode": self._mode,
                "last_update_success": self.last_update_success,
                "update_interval_seconds": (
                    self.update_interval.total_seconds()
                    if self.update_interval
                    else None
                ),
            },
            "state": state_dict,
            "raw_protocol_ring_buffer_size": RAW_PROTOCOL_RING_BUFFER_SIZE,
            "raw_protocol_ring_buffer": self.client.raw_protocol_frames,
        }

    async def async_create_debug_export(self) -> str:
        """Persist the current debug snapshot to HA's local web directory."""
        generated_at = _utcnow_iso()
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        filename = f"xtool-s1-debug-{self.config_entry.entry_id}-{timestamp}.json"
        export_dir = Path(self.hass.config.path("www", "xtool_s1_debug"))
        payload = self.build_debug_export_payload(generated_at=generated_at)
        await self.hass.async_add_executor_job(
            _write_debug_export_file,
            export_dir,
            self.config_entry.entry_id,
            filename,
            payload,
        )
        self._last_debug_export_at = generated_at
        self._last_debug_export_url = f"/local/xtool_s1_debug/{filename}"
        return self._last_debug_export_url

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
            await self._maybe_poll_logfile()
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

    async def _maybe_poll_logfile(self) -> None:
        """Parse per-tool counters from logs.txt and truncate if too large.

        The firmware writes accumulated stats like ``acc_40w_laserworktime``
        to logs.txt periodically. We tail the last 5 KB, parse the
        counters, and if the file exceeds LOGFILE_MAX_SIZE we truncate it
        to prevent it from going stale (the firmware stops writing when
        the file is too big).
        """
        if self._mode == MODE_OFFLINE:
            return
        now = time.monotonic()
        if now - self._last_logfile_poll < LOGFILE_POLL_INTERVAL:
            return
        self._last_logfile_poll = now

        # Check size and truncate if needed
        size = await self.client.fetch_logfile_size()
        if size is not None and size > LOGFILE_MAX_SIZE:
            _LOGGER.info(
                "S1 %s logs.txt is %d bytes (> %d), truncating",
                self.client.host,
                size,
                LOGFILE_MAX_SIZE,
            )
            await self.client.truncate_logfile()

        # Parse the tail for per-tool counters
        tail = await self.client.fetch_logfile_tail()
        if tail is None:
            return
        counters = _parse_logfile_counters(tail)
        if counters:
            self.async_set_updated_data(replace(self.client.state, **counters))

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
