# xTool S1 — Home Assistant Integration

[![CI](https://github.com/hilman2/ha-xtool-s1/actions/workflows/ci.yml/badge.svg)](https://github.com/hilman2/ha-xtool-s1/actions/workflows/ci.yml)
[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Quality Scale](https://img.shields.io/badge/Quality%20Scale-Gold-FFD700.svg)](https://developers.home-assistant.io/docs/core/integration-quality-scale/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen.svg)](#tests)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)

A clean, focused [Home Assistant](https://www.home-assistant.io/) custom
integration for the **xTool S1** laser engraver. Built to the
[HA Quality Scale **Gold**](https://developers.home-assistant.io/docs/core/integration-quality-scale/)
from day one.

> This integration is an independent community project and is **not affiliated
> with xTool**. Use at your own risk — running a laser is your responsibility.

---

## Status

**v1.1** — S1 core, plus job-control buttons, lifetime statistics,
job-state derivation, and a true XCS-app coexistence mode.

What v1.1 adds on top of v1.0:
- **Stop button** (verified `M108` against the live device).
- **Pause / Resume buttons** — provisional placeholders. The exact
  trigger M-codes have not yet been isolated from a packet capture, so
  these go out as buttons that wire your automations *now*; the
  payload constants will be swapped out in a follow-up release without
  breaking anything.
- **Tool detection** — the integration now identifies the installed
  laser head from its firmware fingerprint (e.g. *Diode 40 W*,
  *Infrared 2 W*) and exposes its rated power, capability bitmap and
  per-tool runtime.
- **Lifetime statistics** from `M2008` — total working time,
  standby time, session count, current-tool runtime.
- **Job lifecycle binary sensors** — `paused`, `last_job_aborted`
  (sticky abnormal-finish marker), `job_armed` (job preloaded, waiting
  on the physical Start button).
- **`last_job_outcome` enum** — single sensor that reports the
  most recent job's terminal state (running / paused / completed /
  aborted / idle).
- **True coexist mode** — instead of just backing off, the coordinator
  detects an active XCS session (3+ kicks in 30 s) and stops fighting
  for the WebSocket entirely; HTTP-only entities (light, buttons) keep
  working seamlessly.

Planned for v2:
- AP2 air cleaner support (M9039 push frames)
- Real exhaust-fan / air-assist state — not yet found in any documented
  M-code; needs an active-job packet capture to map. Until then, the
  audible fans on the S1 are not surfaced as sensors.
- Verified Pause / Resume M-codes — see above.

## Coexistence with the XCS app

The S1 firmware has a quirk: while the **xTool Creative Space app** is
actively talking to the laser, it can kick other clients off the
WebSocket on port 8081. This integration is designed to coexist by
running in one of three modes:

- **Normal** — the WebSocket is healthy, push frames update state in
  real time.
- **Coexist** — once we observe 3 kicks within 30 seconds we assume the
  XCS app is open. The coordinator stops trying to keep the WebSocket
  alive and instead runs on a cheap HTTP heartbeat
  (`GET /system?action=mac`). State sensors keep showing their last
  known values (with a `stale: True` attribute), and any
  HTTP-routed entity stays fully operational. Once XCS goes quiet
  again the coordinator opportunistically tries a fresh WS connect
  and snaps back to *Normal*.
- **Offline** — the HTTP heartbeat has been failing for too long.
  Everything goes unavailable until the device comes back.

**Writes** (fill light, Stop / Pause / Resume buttons) all go through
the HTTP gateway on port 8080, so they survive WS kicks regardless of
mode. You can dim the light or stop a job from HA even while XCS is
open and pounding the WebSocket.

---

## Features

- Native config flow — no YAML
- **UDP-broadcast discovery** on port 20000 — finds the S1 on the LAN
  in a single packet, no IP scan
- **Push-based** WebSocket connection on port 8081 — state updates
  arrive in real time
- **HTTP write path** on port 8080 (`POST /cmd`) — write commands
  survive concurrent xTool Creative Space app activity, which
  otherwise kicks the WebSocket
- **Exponential reconnect backoff** (1s → 5s → 15s → 1min → 5min) —
  the integration doesn't fight the app for the WebSocket
- **HTTP heartbeat fallback** — while the WS is being kicked the
  integration stays *available* by polling `GET /system?action=mac`
- Reconfigure flow with serial-number guard for DHCP-shifted devices
- Diagnostic export with the host IP and serial redacted
- English + German UI translations, icon translations, exception translations
- 100 % unit-test coverage on the integration code
- Black + Ruff lint, hassfest + HACS validation in CI

### Entities (per device)

| Type | Name | Unit | Description |
|---|---|---|---|
| sensor | Status | enum | `idle` / `ready` / `measuring` / `preparing` / `frame` / `motion` / `starting` / `running` / `paused` / `finishing` |
| sensor | Last job outcome | enum | `idle` / `running` / `paused` / `completed` / `aborted` |
| sensor | Installed tool | — | Human-readable tool name (`Diode 40 W`, `Infrared 2 W`, …) |
| sensor | Tool power | W | Rated power of the installed laser head |
| sensor | Tool runtime | h | Working seconds of the *current* tool |
| sensor | Working time | h | Lifetime working time across all tools |
| sensor | Standby time | h | Lifetime standby time |
| sensor | Session count | — | Lifetime number of completed sessions |
| sensor | Position X / Y / Z | mm | Current head position |
| sensor | Probe Z | mm | Last Z-probe reading *(diagnostic)* |
| sensor | Light brightness | % | Internal fill-light brightness |
| sensor | Job File | — | Currently loaded job filename |
| sensor | Firmware Version | — | *Diagnostic* |
| sensor | Tool type | — | *Diagnostic* — raw `M54` value |
| sensor | Tool capabilities | — | *Diagnostic, off by default* — raw `M116` bitmap |
| sensor | Tool offset X / Y | mm | *Diagnostic, off by default* — physical mounting offset |
| sensor | Serial number | — | *Diagnostic, off by default* |
| sensor | Auxiliary firmware 1/2, Tool firmware | — | *Diagnostic, off by default* |
| binary_sensor | Running | `running` | A job is actively executing |
| binary_sensor | Paused | — | The current job is paused |
| binary_sensor | Last job aborted | `problem` | The previous job ended via Stop |
| binary_sensor | Job armed | — | Job preloaded — waiting on the physical Start button |
| binary_sensor | Alarm | `problem` | Machine reports an alarm condition |
| binary_sensor | Connection | `connectivity` | WebSocket session is up *(diagnostic)* |
| **light** | Fill light | brightness | Dimmable interior fill light, controllable from HA |
| **button** | Stop | — | Abort the running job (verified `M108`) |
| **button** | Pause | — | Pause the running job (provisional payload) |
| **button** | Resume | — | Resume a paused job (provisional payload) |

---

## Installation

### HACS (recommended)

1. Open HACS → ⋮ → **Custom repositories**
2. Add `https://github.com/hilman2/ha-xtool-s1` as type **Integration**
3. Install → restart Home Assistant
4. **Settings → Devices & Services → Add Integration → "xTool S1"**
5. Either run a **network scan** or enter the laser's IP manually

### Manual

Copy `custom_components/xtool_s1/` into your HA config's `custom_components/`
folder and restart Home Assistant.

---

## Smoke test

After installation, sanity-check the integration on a real laser:

1. **Device shows up** under Settings → Devices → xTool S1 with the correct
   model, firmware version, and serial number.
2. **Status sensor** changes to `running` within a second of starting an
   engraving job (push path is live).
3. **Position X/Y** sensors update continuously while the laser moves.
4. **Alarm sensor** flips to *On* when you open the lid mid-job.
5. **Connection sensor** drops to *Off* when you unplug the laser, then
   recovers to *On* within ~30 seconds after replugging — no HA restart
   required.
6. **Diagnostics export** (Settings → Devices → xTool S1 → ⋮ → Download
   diagnostics) returns a JSON file with the host IP and serial number
   redacted.

---

## Example automations

### Notify when a job finishes

```yaml
alias: xTool S1 — job done
trigger:
  - platform: state
    entity_id: binary_sensor.xtool_s1_running
    from: "on"
    to: "off"
action:
  - service: notify.mobile_app
    data:
      title: "xTool S1"
      message: "Engraving finished."
```

### Turn the exhaust fan on while a job runs

```yaml
alias: xTool S1 — exhaust fan
trigger:
  - platform: state
    entity_id: binary_sensor.xtool_s1_running
action:
  - service: "switch.turn_{{ 'on' if trigger.to_state.state == 'on' else 'off' }}"
    target:
      entity_id: switch.workshop_exhaust_fan
```

### Page on alarm

```yaml
alias: xTool S1 — alarm
trigger:
  - platform: state
    entity_id: binary_sensor.xtool_s1_alarm
    to: "on"
action:
  - service: notify.persistent_notification
    data:
      title: "xTool S1 alarm"
      message: "Lid open or safety stop triggered."
```

---

## Development

Tests run inside WSL Ubuntu with Python 3.13. The venv lives outside `/mnt/d`
to dodge a long-path bug in the WSL ↔ NTFS bridge that strips files from the
installed Home Assistant package.

```bash
./scripts/test.sh              # full suite, parallel, 100 % coverage gate
./scripts/test.sh -k config    # filter to a subset
./scripts/lint.sh              # black + ruff
```

The CI on every push and PR runs:

- `black --check` + `ruff check`
- `home-assistant/actions/hassfest`
- `hacs/action` integration validation
- `pytest -n auto --cov-fail-under=100`

`main` is branch-protected: every job above must pass before a merge is
allowed. Tagged releases (`v*`) re-run the full CI as a gate and only then
publish a GitHub Release with the integration zip.

---

## License

[MIT](./LICENSE)
