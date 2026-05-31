#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
BUILD_DIR="$ROOT_DIR/build"
RELEASE_DIR="$ROOT_DIR/release"
APPDIR="$ROOT_DIR/AppDir"
APPIMAGE_ARCH="${APPIMAGE_ARCH:-x86_64}"
VENV_PATH="$ROOT_DIR/.venv-build"
ICON_PNG="$ROOT_DIR/assets/icons/internet-archive.png"
if [ -n "${BUILD_PYTHON:-}" ]; then
  PYTHON_BIN="$BUILD_PYTHON"
elif [ -x "/usr/bin/python3" ] && /usr/bin/python3 -c "import tkinter" >/dev/null 2>&1; then
  PYTHON_BIN="/usr/bin/python3"
else
  PYTHON_BIN="python3"
fi

case "$APPIMAGE_ARCH" in
  x86_64)
    APPIMAGE_TOOL_ARCH="x86_64"
    ;;
  aarch64|arm64)
    APPIMAGE_ARCH="aarch64"
    APPIMAGE_TOOL_ARCH="aarch64"
    ;;
  *)
    printf 'Unsupported APPIMAGE_ARCH: %s\n' "$APPIMAGE_ARCH" >&2
    printf 'Supported values: x86_64, aarch64 (or arm64)\n' >&2
    exit 1
    ;;
esac

APPIMAGE_TOOL="$ROOT_DIR/appimagetool-${APPIMAGE_TOOL_ARCH}.AppImage"
OUTPUT_APPIMAGE="$RELEASE_DIR/ia-interact-linux-${APPIMAGE_ARCH}.AppImage"
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
"$VENV_PATH/bin/python" -m pip install -r "$ROOT_DIR/requirements-build.txt"

"$VENV_PATH/bin/pyinstaller" \
  --clean \
  --onefile \
  --icon "$ICON_PNG" \
  --name ia-interact \
  --distpath "$DIST_DIR" \
  --workpath "$BUILD_DIR" \
  --specpath "$ROOT_DIR" \
  "$ROOT_DIR/ia-interact.py"

rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin"
mkdir -p "$APPDIR/usr/share/applications"
mkdir -p "$APPDIR/usr/share/icons/hicolor/256x256/apps"

cp "$DIST_DIR/ia-interact" "$APPDIR/usr/bin/ia-interact"
chmod +x "$APPDIR/usr/bin/ia-interact"
cat > "$APPDIR/AppRun" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$HERE/usr/bin/ia-interact"

if [ "$#" -eq 0 ]; then
  if [ -t 0 ] && [ -t 1 ]; then
    exec "$BIN" --cli
  fi
  exec "$BIN" --gui
fi

exec "$BIN" "$@"
EOF
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/ia-interact.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=IA Interact
Exec=ia-interact --gui
Icon=ia-interact
Terminal=false
Categories=Utility;
EOF

cp "$APPDIR/ia-interact.desktop" "$APPDIR/usr/share/applications/ia-interact.desktop"

cp "$ICON_PNG" "$APPDIR/ia-interact.png"

cp "$APPDIR/ia-interact.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/ia-interact.png"

if [[ ! -x "$APPIMAGE_TOOL" ]]; then
  curl -fsSL -o "$APPIMAGE_TOOL" "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-${APPIMAGE_TOOL_ARCH}.AppImage"
  chmod +x "$APPIMAGE_TOOL"
fi

mkdir -p "$RELEASE_DIR"
ARCH="$APPIMAGE_ARCH" APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGE_TOOL" "$APPDIR" "$OUTPUT_APPIMAGE"

printf 'AppImage created at %s\n' "$OUTPUT_APPIMAGE"
