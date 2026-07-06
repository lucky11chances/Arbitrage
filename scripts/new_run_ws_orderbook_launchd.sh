#!/usr/bin/env bash
set -euo pipefail

cd /Users/supremeleader/Desktop/Arbitrage

KEY_ID_FILE="data/new/new_kalshi_api_key_id"
if [[ -z "${KALSHI_API_KEY_ID:-}" ]]; then
  if [[ ! -f "$KEY_ID_FILE" ]]; then
    echo "Missing $KEY_ID_FILE. Run scripts/new_install_ws_orderbook_launch_agent.sh <KALSHI_API_KEY_ID> first." >&2
    exit 1
  fi
  export KALSHI_API_KEY_ID="$(tr -d '[:space:]' < "$KEY_ID_FILE")"
fi

export KALSHI_PRIVATE_KEY_FILE="${KALSHI_PRIVATE_KEY_FILE:-/Users/supremeleader/Desktop/Arbitrage/KS_api_key_test.txt}"

exec ./scripts/new_start_ws_orderbook_sync.sh
