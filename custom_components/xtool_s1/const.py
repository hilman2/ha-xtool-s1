"""Constants for the xTool S1 integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "xtool_s1"

MANUFACTURER: Final = "xTool"
MODEL: Final = "S1"

# WebSocket port the S1 firmware exposes for live state.
WS_PORT: Final = 8081

# DataUpdateCoordinator fallback poll interval.
# State arrives via WebSocket push; this interval only acts as a watchdog
# to detect a dead connection and trigger reconnect.
UPDATE_INTERVAL_SECONDS: Final = 30

# Reconnect back-off ladder (seconds).
RECONNECT_BACKOFF_SECONDS: Final = (1.0, 2.0, 5.0, 10.0)

# Time to wait for the first M2003 reply during config-flow validation.
CONFIG_FLOW_PROBE_TIMEOUT: Final = 8.0

# --- Network scan ----------------------------------------------------------

# Maximum number of hosts a single scan may probe. /22 = 1024 hosts.
# Larger ranges are rejected with a "network_too_large" error.
SCAN_MAX_HOSTS: Final = 1024

# Parallel TCP probes. 64 keeps a /24 scan under ~5s in typical home nets.
SCAN_DEFAULT_CONCURRENCY: Final = 64

# Per-host timeouts (seconds).
SCAN_TCP_TIMEOUT: Final = 0.8
SCAN_WS_TIMEOUT: Final = 3.0

# Translation keys for sensor entities. Each key MUST exist in:
#   - strings.json -> entity.sensor.<key>.name
#   - translations/en.json
#   - translations/de.json
#   - icons.json -> entity.sensor.<key>.default
SENSOR_STATUS: Final = "status"
SENSOR_FIRMWARE_VERSION: Final = "firmware_version"
SENSOR_SERIAL_NUMBER: Final = "serial_number"
SENSOR_TOOL_TYPE: Final = "tool_type"
SENSOR_JOB_FILE: Final = "job_file"
SENSOR_POSITION_X: Final = "position_x"
SENSOR_POSITION_Y: Final = "position_y"
SENSOR_PROBE_Z: Final = "probe_z"
SENSOR_FAN_A: Final = "fan_a"
SENSOR_FAN_B: Final = "fan_b"

BINARY_SENSOR_RUNNING: Final = "running"
BINARY_SENSOR_ALARM: Final = "alarm"
BINARY_SENSOR_CONNECTION: Final = "connection"

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
