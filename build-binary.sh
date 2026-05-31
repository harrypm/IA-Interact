#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="$SCRIPT_DIR/.venv-build"
DIST_PATH="$SCRIPT_DIR/dist"
BUILD_PATH="$SCRIPT_DIR/build"

if [ ! -d "$VENV_PATH" ]; then
  python3 -m venv "$VENV_PATH"
fi
"$VENV_PATH/bin/python" -m pip install --upgrade pip
"$VENV_PATH/bin/python" -m pip install -r "$SCRIPT_DIR/requirements-build.txt"
"$VENV_PATH/bin/pyinstaller" \
  --clean \
  --onefile \
  --name ia-interact \
  --distpath "$DIST_PATH" \
  --workpath "$BUILD_PATH" \
  --specpath "$SCRIPT_DIR" \
  "$SCRIPT_DIR/ia-interact.py"

printf 'Binary created at %s\n' "$SCRIPT_DIR/dist/ia-interact"
