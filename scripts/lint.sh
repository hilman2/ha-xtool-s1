#!/usr/bin/env bash
# Run black + ruff inside WSL Ubuntu against the source tree on /mnt/d.
set -euo pipefail

REPO_WIN_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -W 2>/dev/null || cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_WSL_PATH="$(echo "$REPO_WIN_PATH" | sed -E 's#^([A-Za-z]):#/mnt/\L\1#')"

wsl -d Ubuntu -- bash -lc "set -e; \
  source \$HOME/venvs/ha-xtool-s1/bin/activate; \
  cd '$REPO_WSL_PATH'; \
  black --check custom_components tests; \
  ruff check custom_components tests"
