#!/usr/bin/env bash
# Build "Manuscript Editor.app" (default: on the Desktop).
#   app/build_app.sh                    -> ~/Desktop/Manuscript Editor.app
#   app/build_app.sh /Applications      -> /Applications/Manuscript Editor.app
#
# The app starts server.py from this checkout (if it is not already running)
# and opens the editor in its own window: Chrome or Brave in app mode (no tabs
# or address bar) with a separate profile, or the default browser otherwise.
# It points at this folder, so rebuild it if you move the editor.
set -euo pipefail
EDITOR_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-$HOME/Desktop}"
NAME="Manuscript Editor"
APP="$DEST/$NAME.app"

[ -f "$EDITOR_DIR/app/icon.icns" ] || python3 "$EDITOR_DIR/app/make_icon.py"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
cp "$EDITOR_DIR/app/icon.icns" "$APP/Contents/Resources/icon.icns"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>$NAME</string>
  <key>CFBundleDisplayName</key><string>$NAME</string>
  <key>CFBundleIdentifier</key><string>com.olivercase.manuscript-editor</string>
  <key>CFBundleVersion</key><string>1.0</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleExecutable</key><string>launch</string>
  <key>CFBundleIconFile</key><string>icon</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSUIElement</key><true/>
</dict></plist>
PLIST

sed "s|@EDITOR_DIR@|$EDITOR_DIR|g" "$EDITOR_DIR/app/launch.sh" > "$APP/Contents/MacOS/launch"
chmod +x "$APP/Contents/MacOS/launch"
touch "$APP"
echo "Built $APP"
