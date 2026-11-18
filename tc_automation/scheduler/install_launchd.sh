#!/usr/bin/env bash
# Installs a macOS launchd job that runs the automation every 15 minutes (no manual trigger).
# Usage:  scheduler/install_launchd.sh install | uninstall | status
set -euo pipefail
LABEL="com.alumnx.trainercentral"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
PY="$PROJECT/.venv/bin/python"; [ -x "$PY" ] || PY="$(command -v python3)"
INTERVAL=900   # seconds (assignment: at least every 30 min)

case "${1:-install}" in
  install)
    case "$PROJECT" in "$HOME/Desktop"*|"$HOME/Documents"*|"$HOME/Downloads"*)
      echo "WARNING: $PROJECT is in a macOS-protected folder. launchd jobs may get 'Operation not permitted' there."
      echo "         Either move the project to e.g. ~/trainer_central_automation, or grant Full Disk Access to:"
      echo "         $PY   (System Settings > Privacy & Security > Full Disk Access)";;
    esac
    mkdir -p "$PROJECT/logs" "$HOME/Library/LaunchAgents"
    cat > "$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key><array><string>$PY</string><string>$PROJECT/run.py</string></array>
  <key>WorkingDirectory</key><string>$PROJECT</string>
  <key>StartInterval</key><integer>$INTERVAL</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$PROJECT/logs/launchd.out.log</string>
  <key>StandardErrorPath</key><string>$PROJECT/logs/launchd.err.log</string>
</dict></plist>
PL
    launchctl unload "$PLIST" 2>/dev/null || true
    launchctl load "$PLIST"
    echo "Installed: runs every $((INTERVAL/60)) min. Logs: $PROJECT/logs/trainer_central.log";;
  uninstall)
    launchctl unload "$PLIST" 2>/dev/null || true; rm -f "$PLIST"; echo "Removed $LABEL";;
  status)
    launchctl list | grep "$LABEL" || echo "not loaded"; tail -n 5 "$PROJECT/logs/runs.jsonl" 2>/dev/null || true;;
esac
