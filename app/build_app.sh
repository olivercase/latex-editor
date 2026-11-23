#!/usr/bin/env bash
# Build "Manuscript Editor.app" (default: on the Desktop).
#   app/build_app.sh                    -> ~/Desktop/Manuscript Editor.app
#   app/build_app.sh /Applications      -> /Applications/Manuscript Editor.app
#
# A native window (Swift + the system's WebKit; no browser, no network). It
# starts server.py from this checkout if it is not already running, and stops
# it on quit. Needs the Xcode command line tools (swiftc) to build.
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
  <key>EditorDir</key><string>$EDITOR_DIR</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST

swiftc -O -o "$APP/Contents/MacOS/launch" "$EDITOR_DIR/app/main.swift" -framework Cocoa -framework WebKit
touch "$APP"
echo "Built $APP"
