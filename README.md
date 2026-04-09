# xTool S1 - Home Assistant Integration

[![CI](https://github.com/hilman2/ha-xtool-s1/actions/workflows/ci.yml/badge.svg)](https://github.com/hilman2/ha-xtool-s1/actions/workflows/ci.yml)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Quality Scale](https://img.shields.io/badge/Quality%20Scale-Gold-FFD700.svg)](https://developers.home-assistant.io/docs/core/integration-quality-scale/)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen.svg)](#development)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)

A [Home Assistant](https://www.home-assistant.io/) custom integration for the
**xTool S1** laser engraver. Control your laser, monitor jobs in real time,
and manage saved jobs, all from your phone without the XCS desktop app.

> **Disclaimer**: This is an independent community project and is **not
> affiliated with xTool**. Operating a laser cutter is inherently dangerous.
> Use at your own risk.

---

## Highlights

- **Real-time monitoring** via WebSocket push (status, position, alarm)
- **Job control**: Stop, Pause and Resume buttons that work from HA
- **Job management**: save jobs from the laser, re-start them later with a
  confirmation dialog showing material, thickness and laser module
- **Fill light control**: dimmable light entity with standby detection
- **XCS coexistence**: works alongside the xTool Creative Space desktop app
- **Graceful offline**: a powered-off laser is normal, not an error

---

## Installation

### HACS (recommended)

1. Open **HACS** > **Custom repositories**
2. Add `https://github.com/hilman2/ha-xtool-s1`, type **Integration**
3. Install, then restart Home Assistant
4. **Settings > Devices & Services > Add Integration > xTool S1**
5. Run a **network scan** or enter the laser's IP manually

### Manual

Copy `custom_components/xtool_s1/` into your HA `custom_components/` folder
and restart Home Assistant.

### Configuration

The config flow offers two ways to find the laser:

- **Network scan**: enter a CIDR range (e.g. `192.168.1.0/24`) and the
  integration sends a UDP discovery broadcast on port 20000. Every S1 on
  the network replies with its IP and name.
- **Manual IP**: enter the laser's IP address or hostname directly.

The integration connects via WebSocket (port 8081) and verifies the device
by reading its serial number. No credentials are required.

### Removal

**Settings > Devices & Services > xTool S1 > Delete**

This removes the integration, all entities, and the device. Saved jobs
(from the job management card) are retained in HA storage and can be
cleaned up manually if needed.

---

## Entities

All entities are created automatically per device.

### Sensors

| Name | Description |
|---|---|
| Status | Machine state: idle, ready, measuring, running, paused, ... |
| Last job outcome | idle / running / paused / completed / aborted |
| Installed tool | Detected laser head (e.g. *Diode 40 W*) |
| Working time | Total working hours (lifetime) |
| Session count | Total number of job starts (lifetime) |
| Job file | Currently loaded job filename |
| Light brightness | Fill light percentage (0 when standby) |

<details>
<summary>Diagnostic sensors (hidden by default)</summary>

| Name | Description |
|---|---|
| Firmware version | Main board firmware |
| Serial number | Device serial |
| Tool type, Tool power, Tool capabilities | Raw tool metadata |
| Tool working time | Accumulated working seconds of the current tool type |
| Tool offset X / Y | Physical mounting offset |
| Position X / Y / Z, Probe Z | Head coordinates |
| Standby time, Auxiliary firmware 1/2, Tool firmware | Additional diagnostics |

</details>

### Binary sensors

| Name | Description |
|---|---|
| Running | A job is actively executing |
| Paused | The current job is paused |
| Alarm | Machine reports an alarm (e.g. lid open) |
| Job armed | Job preloaded, waiting for physical Start button |
| Last job aborted | Previous job ended via Stop (sticky) |
| Connection | WebSocket is connected *(diagnostic)* |

### Controls

| Type | Name | Description |
|---|---|---|
| Light | Fill light | Dimmable interior light, detects standby auto-off |
| Button | Stop | Abort the running job |
| Button | Pause | Pause the running job |
| Button | Resume | Resume a paused job |

---

## Job management

The integration can save jobs from the laser, store them in Home Assistant,
and re-run them later. This turns the xTool S1 into a standalone batch
machine: prepare a job once in XCS, save it, then repeat it as many times
as you want from your phone. No computer needed after the initial setup.

### Saving a job

1. Design and run your job in XCS as usual
2. Open the **xTool S1 Jobs** card in your HA dashboard
3. Tap **Job speichern**
4. Fill in the form:
   - **Title**: a short name (e.g. "Phone stand cutout")
   - **Description**: notes for your future self (e.g. "Birch 3 mm, vector cut")
   - **Material**: the material you used (e.g. "Birch plywood")
   - **Thickness**: material thickness in mm
5. Tap **Speichern**

The integration downloads the gcode directly from the laser's SD card and
stores it together with the metadata you entered. The currently installed
laser module (e.g. *Diode 40 W*) is detected and saved automatically.

### Starting a saved job

1. Open the **xTool S1 Jobs** card
2. Find the job in the list and tap **Starten**
3. A confirmation dialog appears showing material, thickness and laser
   module. Verify that the correct material is loaded and the right laser
   head is installed.
4. Tap **Bestatigen & Starten**
5. The integration uploads the gcode to the laser and triggers the start
   sequence
6. **Press the physical Start button on the device** to begin

If the laser module has changed since the job was saved (e.g. you saved
with the 40 W diode but the 2 W IR head is now installed), the integration
blocks the start with a clear error message.

### Batch workflow example

Cutting 20 identical phone stands from plywood:

1. Design the cut in XCS, run it once, save it in HA
2. Remove the finished piece, load new material
3. On your phone: tap **Starten** on the saved job, confirm, press the
   button on the laser
4. Repeat steps 2-3 for all 20 pieces

No computer involved after the initial save.

### Lovelace card setup

The card auto-registers on installation. Add it to any dashboard:

```yaml
type: custom:xtool-s1-jobs-card
```

### Services

The card uses these services under the hood. You can also call them from
automations or scripts:

| Service | Description |
|---|---|
| `xtool_s1.save_job` | Download and store the current job |
| `xtool_s1.start_job` | Upload a saved job and trigger the start sequence |
| `xtool_s1.delete_job` | Remove a saved job |
| `xtool_s1.list_jobs` | List all saved jobs with metadata |

---

## XCS coexistence

The S1 firmware can kick WebSocket clients when the **xTool Creative Space**
desktop app is active. This integration handles it gracefully:

| Mode | Trigger | Behaviour |
|---|---|---|
| **Normal** | WebSocket healthy | Real-time push updates |
| **Coexist** | 3+ WS kicks in 30 s | HTTP heartbeat only; sensors show cached values; light and buttons keep working |
| **Offline** | Device powered off | All dynamic values reset to off/zero; info sensors keep last known values; no errors in HA |

All write operations (light, stop, pause, resume, job upload) go through
HTTP and are unaffected by WebSocket issues.

**Tip**: If XCS is connected via **USB** instead of WiFi, the WebSocket
runs without interruption.

### How data is updated

| Source | Interval | Content |
|---|---|---|
| WebSocket push (port 8081) | Real-time | Status, position, alarm, job file, M22/M323 state |
| Coordinator watchdog poll | 30 s | Reconnects dead sockets, sends M303 ping |
| M2008 stats request | 5 min | Lifetime working time, session count, standby time |
| Logfile tail (`/gcode/logs.txt`) | 10 min | Per-tool working times; truncates file if > 1 MB |

---

## Known limitations

- **Job start requires physical button**: after uploading and triggering
  a job via HA, the user must press the Start button on the device. This
  is a firmware safety feature.
- **XCS over WiFi causes WebSocket kicks**: the XCS desktop app polls
  aggressively and the S1 firmware kicks competing clients. The
  integration detects this and switches to coexist mode automatically.
  Use XCS over USB to avoid this entirely.
- **No HACS store icon**: HACS does not yet support local brand assets
  for custom integrations. The icon shows correctly in HA's own
  integration and device pages.
- **SD card unavailable during jobs**: the logfile and gcode download
  endpoints return errors while a job is running. The integration caches
  the last known values.
- **Single device per entry**: each config entry represents one laser.
  Add multiple entries for multiple devices.

---

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| All sensors show 0 / off | Laser is powered off | Normal, turn on the laser |
| Sensors flicker or go stale | XCS app is open over WiFi | Close XCS or connect it via USB |
| "Connection" sensor stays off | Wrong IP or laser not on network | Reconfigure with correct IP |
| Job card shows no jobs | No jobs saved yet | Save a job after running one in XCS |
| Start button does nothing | Laser not connected via WebSocket | Check connection sensor; restart integration if needed |

---

## Example automations

### Notify when a job finishes

```yaml
trigger:
  - platform: state
    entity_id: binary_sensor.xtool_s1_running
    from: "on"
    to: "off"
action:
  - action: notify.mobile_app
    data:
      title: xTool S1
      message: Job finished.
```

### Exhaust fan follows laser

```yaml
trigger:
  - platform: state
    entity_id: binary_sensor.xtool_s1_running
action:
  - action: "switch.turn_{{ 'on' if trigger.to_state.state == 'on' else 'off' }}"
    target:
      entity_id: switch.workshop_exhaust_fan
```

### Alert on alarm

```yaml
trigger:
  - platform: state
    entity_id: binary_sensor.xtool_s1_alarm
    to: "on"
action:
  - action: notify.persistent_notification
    data:
      title: xTool S1 alarm
      message: Lid open or safety stop triggered.
```

---

## Development

```bash
./scripts/test.sh              # 266 tests, parallel, 100% coverage gate
./scripts/lint.sh              # black + ruff
```

CI runs on every push: black, ruff, hassfest, HACS validation, pytest.
Tagged releases re-run CI before publishing.

---

## License

[MIT](./LICENSE)
