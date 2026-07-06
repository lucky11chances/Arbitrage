#!/usr/bin/env bash
set -euo pipefail

cd /Users/supremeleader/Desktop/Arbitrage

if [[ $# -lt 1 && -z "${KALSHI_API_KEY_ID:-}" ]]; then
  echo "Usage: scripts/new_install_ws_orderbook_launch_agent.sh <KALSHI_API_KEY_ID>" >&2
  echo "Or set KALSHI_API_KEY_ID before running." >&2
  exit 1
fi

mkdir -p data/new "$HOME/Library/LaunchAgents"
chmod +x scripts/new_start_ws_orderbook_sync.sh scripts/new_run_ws_orderbook_launchd.sh

if [[ $# -ge 1 ]]; then
  printf "%s" "$1" > data/new/new_kalshi_api_key_id
elif [[ -n "${KALSHI_API_KEY_ID:-}" ]]; then
  printf "%s" "$KALSHI_API_KEY_ID" > data/new/new_kalshi_api_key_id
fi
chmod 600 data/new/new_kalshi_api_key_id

PLIST="$HOME/Library/LaunchAgents/com.arbitrage.ws-orderbook.plist"
cat > "$PLIST" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.arbitrage.ws-orderbook</string>

  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>/Users/supremeleader/Desktop/Arbitrage/scripts/new_run_ws_orderbook_launchd.sh</string>
  </array>

  <key>WorkingDirectory</key>
  <string>/Users/supremeleader/Desktop/Arbitrage</string>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>/Users/supremeleader/Desktop/Arbitrage/data/new/new_ws_orderbook_launchd.out.log</string>

  <key>StandardErrorPath</key>
  <string>/Users/supremeleader/Desktop/Arbitrage/data/new/new_ws_orderbook_launchd.err.log</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
</dict>
</plist>
PLIST
chmod 644 "$PLIST"

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/com.arbitrage.ws-orderbook"

echo "Installed and started com.arbitrage.ws-orderbook"
echo "Status: launchctl print gui/$(id -u)/com.arbitrage.ws-orderbook"
echo "Logs: data/new/new_ws_orderbook_launchd.out.log and data/new/new_ws_orderbook_launchd.err.log"
