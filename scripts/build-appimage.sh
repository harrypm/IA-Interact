#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
BUILD_DIR="$ROOT_DIR/build"
RELEASE_DIR="$ROOT_DIR/release"
APPDIR="$ROOT_DIR/AppDir"
APPIMAGE_TOOL="$ROOT_DIR/appimagetool-x86_64.AppImage"
OUTPUT_APPIMAGE="$RELEASE_DIR/ia-interact-linux-x86_64.AppImage"

pyinstaller \
  --clean \
  --onefile \
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
ln -s usr/bin/ia-interact "$APPDIR/AppRun"

cat > "$APPDIR/ia-interact.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=IA Interact
Exec=ia-interact
Icon=ia-interact
Terminal=true
Categories=Utility;
EOF

cp "$APPDIR/ia-interact.desktop" "$APPDIR/usr/share/applications/ia-interact.desktop"

if [[ -f "$ROOT_DIR/image.png" ]]; then
  cp "$ROOT_DIR/image.png" "$APPDIR/ia-interact.png"
else
  python - "$APPDIR/ia-interact.png" <<'PY'
import base64
import pathlib
import sys

target = pathlib.Path(sys.argv[1])
target.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9WlAb3kAAAAASUVORK5CYII="))
PY
fi

cp "$APPDIR/ia-interact.png" "$APPDIR/usr/share/icons/hicolor/256x256/apps/ia-interact.png"

if [[ ! -x "$APPIMAGE_TOOL" ]]; then
  curl -fsSL -o "$APPIMAGE_TOOL" "https://github.com/AppImage/AppImageKit/releases/download/continuous/appimagetool-x86_64.AppImage"
  chmod +x "$APPIMAGE_TOOL"
fi

mkdir -p "$RELEASE_DIR"
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 "$APPIMAGE_TOOL" "$APPDIR" "$OUTPUT_APPIMAGE"

printf 'AppImage created at %s\n' "$OUTPUT_APPIMAGE"
