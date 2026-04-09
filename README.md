# xTool S1 — Home Assistant Integration

[![CI](https://github.com/hilman2/ha-xtool-s1/actions/workflows/ci.yml/badge.svg)](https://github.com/hilman2/ha-xtool-s1/actions/workflows/ci.yml)
[![HACS](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Quality Scale](https://img.shields.io/badge/Quality%20Scale-Gold-FFD700.svg)](https://developers.home-assistant.io/docs/core/integration-quality-scale/)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen.svg)](#development)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)

A [Home Assistant](https://www.home-assistant.io/) custom integration for the
**xTool S1** laser engraver. Control your laser, monitor jobs in real time,
and manage saved jobs — all from your phone, no XCS desktop app required.

> **Disclaimer** — This is an independent community project and is **not
> affiliated with xTool**. Operating a laser cutter is inherently dangerous.
> Use at your own risk.

---

## Highlights

- **Real-time monitoring** via WebSocket push — status, position, alarm
- **Job control** — Stop, Pause and Resume buttons that work from HA
- **Job management** — save jobs from the laser, re-start them later with a
  confirmation dialog showing material, thickness and laser module
- **Fill light control** — dimmable light entity with standby detection
- **XCS coexistence** — works alongside the xTool Creative Space desktop app
- **Graceful offline** — a powered-off laser is normal, not an error

---

## Installation

### HACS (recommended)

1. Open **HACS** → ⋮ → **Custom repositories**
2. Add `https://github.com/hilman2/ha-xtool-s1` — type **Integration**
3. Install → restart Home Assistant
4. **Settings → Devices & Services → Add Integration → xTool S1**
5. Run a **network scan** or enter the laser's IP manually

### Manual

Copy `custom_components/xtool_s1/` into your HA `custom_components/` folder
and restart Home Assistant.

---

## Entities

All entities are created automatically per device.

### Sensors

| Name | Description |
|---|---|
| Status | Machine state: idle, ready, measuring, running, paused, … |
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
| Light | Fill light | Dimmable interior light — detects standby auto-off |
| Button | Stop | Abort the running job |
| Button | Pause | Pause the running job |
| Button | Resume | Resume a paused job |

---

## Job management

Save the current job from the laser, then re-run it later — directly from
your phone, no XCS needed.

### Lovelace card

The integration ships a custom card that auto-registers on installation.
Add it to any dashboard:

```yaml
type: custom:xtool-s1-jobs-card
```

The card provides:
- **Save** — downloads the current gcode from the laser and stores it with
  title, description, material and thickness
- **Job list** — shows all saved jobs with their metadata
- **Start** — confirmation dialog showing material, thickness and laser
  module, then uploads the job and triggers the start sequence.
  The user must press the physical Start button on the device.
- **Delete** — remove saved jobs

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
`main` is branch-protected. Tagged releases re-run CI before publishing.

---

## License

[MIT](./LICENSE)
