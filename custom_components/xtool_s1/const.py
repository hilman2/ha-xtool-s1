"""Constants for the xTool S1 integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "xtool_s1"

MANUFACTURER: Final = "xTool"
MODEL: Final = "S1"

# Network ports the S1 firmware exposes:
#   * 8080  -> HTTP REST gateway (POST /cmd write path, GET /system reads)
#   * 8081  -> WebSocket state push (M2003 snapshots, M222/M810/... deltas)
#   * 20000 -> UDP JSON discovery beacon
HTTP_PORT: Final = 8080
WS_PORT: Final = 8081
UDP_DISCOVERY_PORT: Final = 20000

# DataUpdateCoordinator fallback poll interval.
# State arrives via WebSocket push; this interval only acts as a watchdog
# to detect a dead connection and trigger reconnect.
UPDATE_INTERVAL_SECONDS: Final = 30

# Reconnect back-off ladder (seconds). The xTool Creative Space app can
# kick the WebSocket — once we get kicked we don't want to immediately
# fight the app for the connection. The ladder ramps from a quick first
# retry up to 5 minutes for the persistent-app case.
RECONNECT_BACKOFF_SECONDS: Final = (1.0, 5.0, 15.0, 60.0, 300.0)

# Time to wait for the first M2003 reply during config-flow validation.
CONFIG_FLOW_PROBE_TIMEOUT: Final = 8.0

# --- Network scan ----------------------------------------------------------

# Maximum number of hosts a single scan may probe. /22 = 1024 hosts.
# Larger ranges are rejected with a "network_too_large" error.
SCAN_MAX_HOSTS: Final = 1024

# Parallel TCP probes. 64 keeps a /24 scan under ~5s in typical home nets.
SCAN_DEFAULT_CONCURRENCY: Final = 64

# Per-host timeouts (seconds) — used by the legacy TCP scan path.
SCAN_TCP_TIMEOUT: Final = 0.8
SCAN_WS_TIMEOUT: Final = 3.0

# UDP discovery (port 20000) — the S1 replies to a JSON broadcast on the
# local LAN with its IP, name and sub-firmware version. We use this in
# the config-flow scan_network step instead of a TCP/WS sweep.
UDP_DISCOVERY_TIMEOUT: Final = 2.0
UDP_DISCOVERY_BROADCAST_ADDR: Final = "255.255.255.255"

# Translation keys for sensor entities. Each key MUST exist in:
#   - strings.json -> entity.sensor.<key>.name
#   - translations/en.json
#   - translations/de.json
#   - icons.json -> entity.sensor.<key>.default
SENSOR_STATUS: Final = "status"
SENSOR_FIRMWARE_VERSION: Final = "firmware_version"
SENSOR_FIRMWARE_AUX_1: Final = "firmware_aux_1"
SENSOR_FIRMWARE_AUX_2: Final = "firmware_aux_2"
SENSOR_FIRMWARE_TOOL: Final = "firmware_tool"
SENSOR_SERIAL_NUMBER: Final = "serial_number"
SENSOR_TOOL_TYPE: Final = "tool_type"
SENSOR_JOB_FILE: Final = "job_file"
SENSOR_POSITION_X: Final = "position_x"
SENSOR_POSITION_Y: Final = "position_y"
SENSOR_PROBE_Z: Final = "probe_z"
SENSOR_LIGHT_BRIGHTNESS: Final = "light_brightness"

BINARY_SENSOR_RUNNING: Final = "running"
BINARY_SENSOR_ALARM: Final = "alarm"
BINARY_SENSOR_CONNECTION: Final = "connection"

LIGHT_FILL_LIGHT: Final = "fill_light"

# Status enum values reported via the `status` sensor.
# These MUST be lowercase, snake_case, and matched 1:1 in
# strings.json -> entity.sensor.status.state.<value>.
STATUS_IDLE: Final = "idle"
STATUS_READY: Final = "ready"
STATUS_MEASURING: Final = "measuring"
STATUS_STARTING: Final = "starting"
STATUS_RUNNING: Final = "running"
STATUS_FINISHING: Final = "finishing"
STATUS_UNKNOWN: Final = "unknown"

STATUS_OPTIONS: Final = (
    STATUS_IDLE,
    STATUS_READY,
    STATUS_MEASURING,
    STATUS_STARTING,
    STATUS_RUNNING,
    STATUS_FINISHING,
    STATUS_UNKNOWN,
)

# Mapping of S1 work-state codes (from M222) to status enum values.
# Source: BassXT/xtool#23 work-state observations.
WORK_STATE_MAP: Final[dict[str, str]] = {
    "S1": STATUS_READY,
    "S3": STATUS_IDLE,
    "S10": STATUS_MEASURING,
    "S13": STATUS_STARTING,
    "S14": STATUS_RUNNING,
    "S19": STATUS_FINISHING,
}

# Work-state codes that count as "the laser is actively running a job".
RUNNING_WORK_STATES: Final = frozenset({"S13", "S14", "S19"})
