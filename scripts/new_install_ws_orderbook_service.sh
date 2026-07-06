#!/usr/bin/env bash
set -euo pipefail

cd /Users/supremeleader/Desktop/Arbitrage

EXISTING_KEY_ID_FILE="data/new/new_kalshi_api_key_id"
if [[ $# -lt 1 && -z "${KALSHI_API_KEY_ID:-}" && ! -f "$EXISTING_KEY_ID_FILE" ]]; then
  echo "Usage: scripts/new_install_ws_orderbook_service.sh <KALSHI_API_KEY_ID>" >&2
  echo "Or set KALSHI_API_KEY_ID before running." >&2
  echo "Or keep the key id in data/new/new_kalshi_api_key_id." >&2
  exit 1
fi

SERVICE_ROOT="${ARB_WS_SERVICE_ROOT:-$HOME/Library/Application Support/ArbitrageWS}"
RUNTIME_DIR="$SERVICE_ROOT/runtime"
DATA_DIR="$SERVICE_ROOT/data/new"
SECRETS_DIR="$SERVICE_ROOT/secrets"
LOG_DIR="$SERVICE_ROOT/logs"
PLIST="$HOME/Library/LaunchAgents/com.arbitrage.ws-orderbook.plist"

mkdir -p "$RUNTIME_DIR/scripts" "$DATA_DIR" "$SECRETS_DIR" "$LOG_DIR" "$HOME/Library/LaunchAgents"

cp scripts/new_ws_orderbook_core.py "$RUNTIME_DIR/scripts/"
cp scripts/new_bootstrap_ws_database.py "$RUNTIME_DIR/scripts/"
cp scripts/new_run_ws_orderbook_collector.py "$RUNTIME_DIR/scripts/"
cp scripts/new_run_ws_orderbook_service.sh "$RUNTIME_DIR/"
chmod +x "$RUNTIME_DIR/new_run_ws_orderbook_service.sh"

if [[ ! -d .venv-ws ]]; then
  echo "Missing .venv-ws; create it and install websockets first." >&2
  exit 1
fi
rm -rf "$SERVICE_ROOT/.venv-ws"
ditto .venv-ws "$SERVICE_ROOT/.venv-ws"

if [[ $# -ge 1 ]]; then
  printf "%s" "$1" > "$SECRETS_DIR/kalshi_api_key_id"
elif [[ -n "${KALSHI_API_KEY_ID:-}" ]]; then
  printf "%s" "$KALSHI_API_KEY_ID" > "$SECRETS_DIR/kalshi_api_key_id"
else
  tr -d '[:space:]' < "$EXISTING_KEY_ID_FILE" > "$SECRETS_DIR/kalshi_api_key_id"
fi
chmod 600 "$SECRETS_DIR/kalshi_api_key_id"

SOURCE_PRIVATE_KEY="${KALSHI_PRIVATE_KEY_FILE:-/Users/supremeleader/Desktop/Arbitrage/KS_api_key_test.txt}"
if [[ ! -f "$SOURCE_PRIVATE_KEY" ]]; then
  echo "Missing Kalshi private key file: $SOURCE_PRIVATE_KEY" >&2
  exit 1
fi
cp "$SOURCE_PRIVATE_KEY" "$SECRETS_DIR/kalshi_private_key.pem"
chmod 600 "$SECRETS_DIR/kalshi_private_key.pem"

.venv-ws/bin/python scripts/new_export_ws_metadata.py \
  --source data/new/new_arb_research.sqlite \
  --dest "$DATA_DIR/new_arb_research.sqlite"

cat > "$PLIST" <<PLIST
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
    <string>$RUNTIME_DIR/new_run_ws_orderbook_service.sh</string>
  </array>

  <key>WorkingDirectory</key>
  <string>$RUNTIME_DIR</string>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <true/>

  <key>StandardOutPath</key>
  <string>$LOG_DIR/new_ws_orderbook.out.log</string>

  <key>StandardErrorPath</key>
  <string>$LOG_DIR/new_ws_orderbook.err.log</string>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>ARB_WS_SERVICE_ROOT</key>
    <string>$SERVICE_ROOT</string>
  </dict>
</dict>
</plist>
PLIST
chmod 644 "$PLIST"

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
launchctl kickstart -k "gui/$(id -u)/com.arbitrage.ws-orderbook"

echo "Installed and started com.arbitrage.ws-orderbook"
echo "Service root: $SERVICE_ROOT"
echo "Status CSV: $DATA_DIR/new_latest_ws_status.csv"
echo "Logs: $LOG_DIR/new_ws_orderbook.out.log and $LOG_DIR/new_ws_orderbook.err.log"
