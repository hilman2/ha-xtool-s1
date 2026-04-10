# ha-xtool-s1 Project Instructions

## Repository and User Context

- Repository: `hilman2/ha-xtool-s1`
- Local workspace: `D:\Git\ha-xtool-s1`
- Home Assistant domain: `xtool_s1`
- License: MIT
- Project type: greenfield HACS custom integration for the xTool S1
- Current quality target/status: Home Assistant Quality Scale Platinum
- Primary language with the user: German
- Code, comments, commit titles, and PR text: English unless the user asks otherwise
- Real hardware exists and smoke tests against the user's xTool S1 are possible and expected when useful

## GitHub Workflow

- Before starting any work, always sync with GitHub first.
- Minimum first step: `git fetch origin --prune`
- If local `main` is checked out and clean, fast-forward it first: `git pull --ff-only origin main`
- Never work directly on `main`.
- Do all changes on a branch.
- Open or update a Pull Request for every change set.
- Do not merge locally unless the user explicitly asks.
- Treat GitHub as the source of truth for current state, open PRs, branch protection, CI, and releases.

### Branch and PR Rules

- `main` is protected and must stay green.
- Required GitHub checks are:
  - `Lint (black + ruff)`
  - `Hassfest`
  - `HACS validation`
  - `Tests (Python 3.13)`
- PR conversations must be resolved before merge.
- Keep the branch up to date with `main` before merge if GitHub requires it.

### Release Rules

- Tags matching `v*` trigger the release workflow automatically.
- The release workflow re-runs CI as a gate.
- The release workflow verifies `custom_components/xtool_s1/manifest.json` matches the tag version.
- The release workflow builds the GitHub release ZIP automatically.
- Only create release tags intentionally and only as part of the planned release process.

## Commit Identity and Attribution

- All commits in this repo must be authored as `hilman2` only.
- Never add `Co-Authored-By: Claude ...` trailers.
- Never add "Generated with Claude Code" style badges or attribution blocks.
- Use the repo-local Git identity for this project, not the user's global personal identity.
- Preferred repo-local Git identity:
  - `user.name = hilman2`
  - `user.email = 79746653+hilman2@users.noreply.github.com`

## Environment and Tooling

### WSL Only

- All Python development tooling runs in WSL Ubuntu, never with native Windows Python.
- Always call WSL explicitly with `wsl -d Ubuntu` because the default distro may be `docker-desktop`.
- Virtual environments live in `~/venvs/<project>` inside WSL, not in the repo on `/mnt/d`.
- Convert repo paths like `D:\Git\ha-xtool-s1` to `/mnt/d/Git/ha-xtool-s1` inside WSL.

### Standard Command Pattern

```bash
wsl -d Ubuntu -- bash -lc 'source ~/venvs/<project>/bin/activate && cd /mnt/d/Git/ha-xtool-s1 && <command>'
```

### Python Quality Rules

- Use Black formatting and Ruff linting for Python code.
- Target Python 3.13.
- Prefer fixing code cleanly over adding `# noqa`.
- Tests use `pytest-homeassistant-custom-component`.
- Test execution assumes `pytest-xdist`; tests must stay isolated.
- Coverage gate is 100%.

## Windows and Deployment Notes

### SSH / All-Inkl

- Host: `w01a0e03.kasserver.com`
- User: `ssh-w01a0e03`
- SSH config alias: `allinkl`
- Key: `~/.ssh/id_ed25519`
- Auth requires both key and password

### rsync on Windows

- Windows has no native `rsync`; use WSL Ubuntu.
- When `rsync` in WSL needs SSH transport, use Windows OpenSSH:

```bash
wsl -d Ubuntu rsync -e /mnt/c/Windows/System32/OpenSSH/ssh.exe ...
```

## README and User-Facing Documentation Rules

- Do not add a `Credits`, `Acknowledgements`, or similar section to `README.md` unless the user explicitly asks.
- Keep third-party attribution out of the README by default.
- Technical attribution or provenance can still live in source comments, commit history, PRs, or internal docs when needed.

## Home Assistant Architecture Defaults

- Prefer modern Home Assistant patterns, not legacy ones.
- Default to:
  - `runtime_data`
  - typed config entries
  - `DataUpdateCoordinator`
  - `_attr_has_entity_name`
  - `translation_key` with `strings.json` and translations
  - `icons.json`
  - `quality_scale.yaml`
- Avoid old `hass.data[DOMAIN]` style patterns unless there is a strong repo-local reason.

## Scope Decisions

### S1 First, AP2 Later

- AP2 air cleaner support is intentionally deferred to v2 unless the user explicitly reprioritizes it.
- Do not expand AP2-related parsing, entities, or config flow in normal v1 work.
- A small `v2` marker in code is fine if it helps future work.
- If AP2 becomes active scope later, it will include `M9039`, purifier/filter entities, and additional config/options flow work.

## Protocol and Product Knowledge

### Source of Truth

- `docs/PROTOCOL.md` is the living in-repo protocol reference and should be preferred over scattered notes.
- BassXT `xtool` PR `#23` is the historical external reference for the early S1 WebSocket protocol and parser ideas.
- BassXT is a protocol reference, not a template for repo structure or architecture decisions in this project.

### Important S1 Facts

- WebSocket is on port `8081`.
- HTTP gateway is on port `8080`.
- UDP discovery is on port `20000`.
- `GET /system?action=*`, `POST /cmd`, `POST /upload`, `GET /gcode/`, and related file-server endpoints are all relevant surfaces.
- `GET /gcode/` exposes the SD-card-backed file server.
- `logs.txt` on the file server is a major source of reverse-engineering truth.

### Known Protocol Truths

- `M13` is fill-light brightness, not exhaust fan state.
- The integration should expose one real fill-light control instead of misleading fan sensors.
- Real exhaust fan state is still not reliably mapped and should not be guessed.
- `M2003` is the full status snapshot.
- `M303` is the position refresh / keepalive path.
- `M2008` carries important counters.
- `M1109` is a high-precision tool-offset related frame and is useful for diagnostics.
- `/gcode/logs.txt` contains M53 events, M222 state transitions, fire-alarm traces, and per-tool counters.

### Current Tooling / Detection Knowledge

- Prefer empirically verified protocol findings over RepRap assumptions.
- When a field was disproved by later captures, do not keep using the old assumption just because earlier notes or fixtures did.
- For tool identification, rely on the protocol findings documented in `docs/PROTOCOL.md`, not on outdated assumptions.

## Additional Project References

- HACS custom integrations no longer need a `home-assistant/brands` repo entry as of Home Assistant 2026.3.
- Do not open a brands PR for this repo unless requirements change again.
- The project is intentionally published as `xtool_s1` to avoid HACS collisions with BassXT's `xtool`.

## Practical Session Checklist

1. Sync with GitHub first.
2. Confirm you are not working on `main`.
3. Review open issue / PR / workflow context when relevant.
4. Use WSL Ubuntu for every Python tool command.
5. Keep commit identity as `hilman2` only.
6. Keep README free of unsolicited credits.
7. Respect current scope boundaries, especially AP2 deferral.
8. Prefer verified protocol knowledge from `docs/PROTOCOL.md` and the captured references.
