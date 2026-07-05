#!/usr/bin/env bash
# Build a .deb from the PyInstaller --onedir output.
#
# Usage:  packaging/linux/build_deb.sh <version> <onedir_path> <icon_png> <output_deb>
#   version      release version without a leading "v" (e.g. 1.4.0)
#   onedir_path  PyInstaller onedir dir, e.g. dist/SubtitleTranslator
#   icon_png     256x256 PNG icon
#   output_deb   destination path, e.g. dist/SubtitleTranslator-linux.deb
#
# Layout produced:
#   /opt/SubtitleTranslator/<onedir contents>
#   /usr/bin/subtitle-translator            (wrapper -> exec the app)
#   /usr/share/applications/*.desktop
#   /usr/share/icons/hicolor/256x256/apps/subtitle-translator.png
set -euo pipefail

VERSION="${1:?version required}"
ONEDIR="${2:?onedir path required}"
ICON_PNG="${3:?icon png required}"
OUTPUT_DEB="${4:?output deb path required}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PKGROOT="$(mktemp -d)"
trap 'rm -rf "$PKGROOT"' EXIT
chmod 755 "$PKGROOT"

# --- payload ---
install -d "$PKGROOT/opt/SubtitleTranslator"
cp -r "$ONEDIR"/. "$PKGROOT/opt/SubtitleTranslator/"

install -d "$PKGROOT/usr/bin"
cat > "$PKGROOT/usr/bin/subtitle-translator" <<'WRAP'
#!/bin/sh
exec /opt/SubtitleTranslator/SubtitleTranslator "$@"
WRAP
chmod 755 "$PKGROOT/usr/bin/subtitle-translator"

install -d "$PKGROOT/usr/share/applications"
cp "$HERE/subtitle-translator.desktop" "$PKGROOT/usr/share/applications/subtitle-translator.desktop"

install -d "$PKGROOT/usr/share/icons/hicolor/256x256/apps"
cp "$ICON_PNG" "$PKGROOT/usr/share/icons/hicolor/256x256/apps/subtitle-translator.png"

# --- control ---
install -d "$PKGROOT/DEBIAN"
sed "s/__VERSION__/$VERSION/" "$HERE/control.template" > "$PKGROOT/DEBIAN/control"

# --- build ---
dpkg-deb --root-owner-group --build "$PKGROOT" "$OUTPUT_DEB"
echo "Built $OUTPUT_DEB"
