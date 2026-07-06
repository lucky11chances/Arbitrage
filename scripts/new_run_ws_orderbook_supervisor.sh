#!/usr/bin/env bash
set -euo pipefail

cd /Users/supremeleader/Desktop/Arbitrage

mkdir -p data/new
ARB_WS_SPORTS="${ARB_WS_SPORTS:-tennis,esports,baseball}"
ARB_WS_SNAPSHOT_INTERVAL="${ARB_WS_SNAPSHOT_INTERVAL:-5}"
ARB_WS_MAX_STALE_SECONDS="${ARB_WS_MAX_STALE_SECONDS:-60}"

if [[ -z "${KALSHI_API_KEY_ID:-}" ]]; then
  if [[ ! -f data/new/new_kalshi_api_key_id ]]; then
    echo "Missing data/new/new_kalshi_api_key_id or KALSHI_API_KEY_ID" >&2
    exit 1
  fi
  export KALSHI_API_KEY_ID="$(tr -d '[:space:]' < data/new/new_kalshi_api_key_id)"
fi

export KALSHI_PRIVATE_KEY_FILE="${KALSHI_PRIVATE_KEY_FILE:-/Users/supremeleader/Desktop/Arbitrage/KS_api_key_test.txt}"

if [[ ! -f "$KALSHI_PRIVATE_KEY_FILE" ]]; then
  echo "Missing Kalshi private key file: $KALSHI_PRIVATE_KEY_FILE" >&2
  exit 1
fi
chmod 600 "$KALSHI_PRIVATE_KEY_FILE"

while true; do
  echo "$(date -u +%FT%TZ) supervisor bootstrap"
  .venv-ws/bin/python scripts/new_bootstrap_ws_database.py \
    --db data/new/new_arb_research.sqlite \
    --sports "$ARB_WS_SPORTS" \
    --skip-discovery

  echo "$(date -u +%FT%TZ) supervisor start collector sports=$ARB_WS_SPORTS"
  set +e
  .venv-ws/bin/python scripts/new_run_ws_orderbook_collector.py \
    --db data/new/new_arb_research.sqlite \
    --sports "$ARB_WS_SPORTS" \
    --snapshot-interval "$ARB_WS_SNAPSHOT_INTERVAL" \
    --depth 4 \
    --metadata-refresh-seconds 300 \
    --status-output data/new/new_latest_ws_status.csv \
    --max-stale-seconds "$ARB_WS_MAX_STALE_SECONDS" \
    --skip-metadata-refresh \
    --pm-chunk-size 2000 \
    --ks-chunk-size 2000 \
    --subscription-start-stagger-seconds 1.0 \
    --ws-max-size-bytes 16777216 \
    --reconnect-seconds 10 \
    --reconnect-max-seconds 120 \
    --reconnect-jitter-seconds 5
  exit_code=$?
  set -e

  echo "$(date -u +%FT%TZ) collector exited with code $exit_code; restarting in 5s"
  sleep 5
done
