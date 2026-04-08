# xTool S1 — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/)
[![Quality Scale](https://img.shields.io/badge/Quality%20Scale-Gold-FFD700.svg)](https://developers.home-assistant.io/docs/core/integration-quality-scale/)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](./LICENSE)

A clean, focused [Home Assistant](https://www.home-assistant.io/) custom integration
for the **xTool S1** laser engraver. Built to the
[HA Quality Scale **Gold**](https://developers.home-assistant.io/docs/core/integration-quality-scale/)
from day one.

> This integration is an independent community project and is **not affiliated
> with xTool**.

---

## Status

v1.0 — S1 core only. AP2 air cleaner support is planned for v2.

---

## Features

- Native config flow (no YAML)
- Push-based WebSocket connection (port 8081) — state updates arrive in real time
- Auto-reconnect with exponential backoff
- Diagnostic export (with sensitive data redacted)
- English + German UI translations
- 95 %+ test coverage

### Entities (per device)

| Type | Name | Unit | Description |
|---|---|---|---|
| sensor | Status | enum | `idle` / `ready` / `measuring` / `starting` / `running` / `finishing` |
| sensor | Position X | mm | Current X-axis position |
| sensor | Position Y | mm | Current Y-axis position |
| sensor | Probe Z | mm | Last Z-probe reading (diagnostic, off by default) |
| sensor | Fan A | % | Internal fan A speed |
| sensor | Fan B | % | Internal fan B speed |
| sensor | Job File | — | Currently loaded job filename |
| sensor | Firmware Version | — | Diagnostic |
| sensor | Serial Number | — | Diagnostic, off by default |
| sensor | Tool Type | — | Diagnostic |
| binary_sensor | Running | running | Job is actively executing |
| binary_sensor | Alarm | problem | Machine reports an alarm condition |
| binary_sensor | Connection | connectivity | WebSocket session up (diagnostic) |

---

## Installation

### HACS (recommended)

1. Open HACS → ⋮ → **Custom repositories**
2. Add `https://github.com/hilman2/ha-xtool-s1` as type **Integration**
3. Install → restart Home Assistant
4. **Settings → Devices & Services → Add Integration → "xTool S1"**
5. Enter the laser's IP address

### Manual

Copy `custom_components/xtool_s1/` into your HA config's `custom_components/`
folder and restart.

---

## Development

Tests run inside WSL Ubuntu with Python 3.13.

```bash
./scripts/test.sh              # full suite, parallel, with coverage gate
./scripts/test.sh -k config    # filter to a single test file
./scripts/lint.sh              # black + ruff
```

See [`CONTRIBUTING.md`](./CONTRIBUTING.md) (coming) for the architecture
overview.

---


## License

[MIT](./LICENSE)
