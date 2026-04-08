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

**v1.0** — S1 core only. AP2 air cleaner support is planned for v2.

---

## Features

- Native config flow — no YAML
- **Network scan** for the S1 (port 8081 is rare in home nets) or manual IP entry
- **Push-based** WebSocket connection — state updates arrive in real time
- 30-second watchdog poll auto-reconnects on dropped sockets
- Reconfigure flow with serial-number guard for DHCP-shifted devices
- Diagnostic export with the host IP and serial redacted
- English + German UI translations, icon translations, exception translations
- 100 % unit-test coverage on the integration code
- Black + Ruff lint, hassfest + HACS validation in CI

### Entities (per device)

| Type | Name | Unit | Description |
|---|---|---|---|
| sensor | Status | enum | `idle` / `ready` / `measuring` / `starting` / `running` / `finishing` |
| sensor | Position X | mm | Current X-axis position |
| sensor | Position Y | mm | Current Y-axis position |
| sensor | Probe Z | mm | Last Z-probe reading *(diagnostic, off by default)* |
| sensor | Fan A | % | Internal fan A speed |
| sensor | Fan B | % | Internal fan B speed |
| sensor | Job File | — | Currently loaded job filename |
| sensor | Firmware Version | — | *Diagnostic* |
| sensor | Serial Number | — | *Diagnostic, off by default* |
| sensor | Tool Type | — | *Diagnostic* |
| binary_sensor | Running | `running` | A job is actively executing |
| binary_sensor | Alarm | `problem` | Machine reports an alarm condition |
| binary_sensor | Connection | `connectivity` | WebSocket session is up *(diagnostic)* |

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
