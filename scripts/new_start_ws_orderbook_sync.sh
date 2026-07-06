#!/usr/bin/env bash
set -euo pipefail

cd /Users/supremeleader/Desktop/Arbitrage

if [[ ! -x ".venv-ws/bin/python" ]]; then
  echo "Missing .venv-ws. Create it and install websockets first." >&2
  exit 1
fi

if [[ -z "${KALSHI_API_KEY_ID:-}" ]]; then
  if [[ $# -lt 1 ]]; then
    echo "Usage: scripts/new_start_ws_orderbook_sync.sh <KALSHI_API_KEY_ID>" >&2
    echo "Or set KALSHI_API_KEY_ID before running." >&2
    exit 1
  fi
  export KALSHI_API_KEY_ID="$1"
fi

export KALSHI_PRIVATE_KEY_FILE="${KALSHI_PRIVATE_KEY_FILE:-/Users/supremeleader/Desktop/Arbitrage/KS_api_key_test.txt}"

if [[ ! -f "$KALSHI_PRIVATE_KEY_FILE" ]]; then
  echo "Missing Kalshi private key file: $KALSHI_PRIVATE_KEY_FILE" >&2
  exit 1
fi

chmod 600 "$KALSHI_PRIVATE_KEY_FILE"
mkdir -p data/new
ARB_WS_SPORTS="${ARB_WS_SPORTS:-tennis,esports,baseball}"
ARB_WS_SNAPSHOT_INTERVAL="${ARB_WS_SNAPSHOT_INTERVAL:-5}"
ARB_WS_MAX_STALE_SECONDS="${ARB_WS_MAX_STALE_SECONDS:-60}"

echo "$(date -u +%FT%TZ) bootstrap ws database sports=$ARB_WS_SPORTS"
.venv-ws/bin/python scripts/new_bootstrap_ws_database.py \
  --db data/new/new_arb_research.sqlite \
  --sports "$ARB_WS_SPORTS" \
  --skip-discovery

echo "$(date -u +%FT%TZ) start ws orderbook collector sports=$ARB_WS_SPORTS"
exec .venv-ws/bin/python scripts/new_run_ws_orderbook_collector.py \
  --db data/new/new_arb_research.sqlite \
  --sports "$ARB_WS_SPORTS" \
  --snapshot-interval "$ARB_WS_SNAPSHOT_INTERVAL" \
  --depth 4 \
  --metadata-refresh-seconds 300 \
  --status-output data/new/new_latest_ws_status.csv \
  --max-stale-seconds "$ARB_WS_MAX_STALE_SECONDS"
