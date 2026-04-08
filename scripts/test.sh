#!/usr/bin/env bash
# Run the test suite inside WSL Ubuntu.
# Usage:
#   ./scripts/test.sh                 # full parallel suite with coverage gate
#   ./scripts/test.sh -k config_flow  # filter; flags pass through to pytest
#
# The venv lives at ~/venvs/ha-xtool-s1 inside WSL — NOT inside the
# repo on /mnt/d. This sidesteps the WSL+NTFS long-path bug that
# silently drops some Home Assistant helper files at install time.

set -euo pipefail

REPO_WIN_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -W 2>/dev/null || cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_WSL_PATH="$(echo "$REPO_WIN_PATH" | sed -E 's#^([A-Za-z]):#/mnt/\L\1#')"

wsl -d Ubuntu -- bash -lc "set -e; \
  VENV=\$HOME/venvs/ha-xtool-s1; \
  if [ ! -d \"\$VENV\" ]; then \
    mkdir -p \"\$HOME/venvs\"; \
    uv venv --python 3.13 \"\$VENV\"; \
    source \"\$VENV/bin/activate\"; \
    uv pip install -r '$REPO_WSL_PATH/requirements_test.txt'; \
  else \
    source \"\$VENV/bin/activate\"; \
  fi; \
  cd '$REPO_WSL_PATH'; \
  pytest $*"
