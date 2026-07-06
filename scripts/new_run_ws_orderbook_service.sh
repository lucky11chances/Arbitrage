#!/usr/bin/env bash
set -euo pipefail

SERVICE_ROOT="${ARB_WS_SERVICE_ROOT:-$HOME/Library/Application Support/ArbitrageWS}"
RUNTIME_DIR="$SERVICE_ROOT/runtime"
DATA_DIR="$SERVICE_ROOT/data/new"
SECRETS_DIR="$SERVICE_ROOT/secrets"

cd "$RUNTIME_DIR"

export KALSHI_API_KEY_ID="$(tr -d '[:space:]' < "$SECRETS_DIR/kalshi_api_key_id")"
export KALSHI_PRIVATE_KEY_FILE="$SECRETS_DIR/kalshi_private_key.pem"
ARB_WS_SPORTS="${ARB_WS_SPORTS:-tennis,esports,baseball}"
ARB_WS_SNAPSHOT_INTERVAL="${ARB_WS_SNAPSHOT_INTERVAL:-5}"
ARB_WS_MAX_STALE_SECONDS="${ARB_WS_MAX_STALE_SECONDS:-60}"

"$SERVICE_ROOT/.venv-ws/bin/python" scripts/new_bootstrap_ws_database.py \
  --db "$DATA_DIR/new_arb_research.sqlite" \
  --sports "$ARB_WS_SPORTS" \
  --skip-discovery

exec "$SERVICE_ROOT/.venv-ws/bin/python" scripts/new_run_ws_orderbook_collector.py \
  --db "$DATA_DIR/new_arb_research.sqlite" \
  --sports "$ARB_WS_SPORTS" \
  --snapshot-interval "$ARB_WS_SNAPSHOT_INTERVAL" \
  --depth 4 \
  --metadata-refresh-seconds 300 \
  --status-output "$DATA_DIR/new_latest_ws_status.csv" \
  --max-stale-seconds "$ARB_WS_MAX_STALE_SECONDS" \
  --skip-metadata-refresh \
  --pm-chunk-size 500 \
  --ks-chunk-size 500
