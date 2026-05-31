#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="$SCRIPT_DIR/.venv-build"
DIST_PATH="$SCRIPT_DIR/dist"
BUILD_PATH="$SCRIPT_DIR/build"
ICON_PNG="$SCRIPT_DIR/assets/icons/internet-archive.png"
if [ -n "${BUILD_PYTHON:-}" ]; then
  PYTHON_BIN="$BUILD_PYTHON"
elif [ -x "/usr/bin/python3" ] && /usr/bin/python3 -c "import tkinter" >/dev/null 2>&1; then
  PYTHON_BIN="/usr/bin/python3"
else
  PYTHON_BIN="python3"
fi

if [ -d "$VENV_PATH" ] && ! "$VENV_PATH/bin/python" -c "import tkinter" >/dev/null 2>&1; then
  rm -rf "$VENV_PATH"
fi
if [ ! -d "$VENV_PATH" ]; then
  "$PYTHON_BIN" -m venv "$VENV_PATH"
fi

if [ ! -f "$ICON_PNG" ]; then
  printf 'Missing icon asset: %s\n' "$ICON_PNG" >&2
  exit 1
fi
"$VENV_PATH/bin/python" -m pip install --upgrade pip
"$VENV_PATH/bin/python" -m pip install -r "$SCRIPT_DIR/requirements-build.txt"
"$VENV_PATH/bin/pyinstaller" \
  --clean \
  --onefile \
  --icon "$ICON_PNG" \
  --name ia-interact \
  --distpath "$DIST_PATH" \
  --workpath "$BUILD_PATH" \
  --specpath "$SCRIPT_DIR" \
  "$SCRIPT_DIR/ia-interact.py"

printf 'Binary created at %s\n' "$SCRIPT_DIR/dist/ia-interact"
