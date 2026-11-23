#!/bin/bash
# Launcher inside "Manuscript Editor.app" (build_app.sh fills in EDITOR_DIR).
EDITOR_DIR="@EDITOR_DIR@"
PORT=8377
URL="http://127.0.0.1:$PORT/"
LOG="$HOME/Library/Logs/manuscript-editor.log"
export PATH="/opt/homebrew/bin:/usr/local/bin:/Library/TeX/texbin:/usr/bin:/bin:/usr/sbin:/sbin"

fail() { osascript -e "display alert \"Manuscript Editor\" message \"$1\" as critical" >/dev/null; exit 1; }

[ -f "$EDITOR_DIR/server.py" ] || fail "Cannot find the editor at $EDITOR_DIR. Run app/build_app.sh again."

up() { curl -fs -m 2 "http://127.0.0.1:$PORT/api/papers" >/dev/null 2>&1; }

if ! up; then
  PY="$(command -v python3)"
  echo "---- $(date) starting server" >> "$LOG"
  nohup "$PY" "$EDITOR_DIR/server.py" --port "$PORT" --no-open >> "$LOG" 2>&1 &
  for _ in $(seq 1 50); do up && break; sleep 0.2; done
  up || fail "The editor server did not start. See ~/Library/Logs/manuscript-editor.log"
fi

PROFILE="$HOME/Library/Application Support/Manuscript Editor/browser"
for B in "Google Chrome" "Brave Browser" "Microsoft Edge"; do
  if [ -d "/Applications/$B.app" ]; then
    exec open -na "$B" --args --app="$URL" --user-data-dir="$PROFILE" --no-first-run --no-default-browser-check
  fi
done
exec open "$URL"
