#!/usr/bin/env bash
set -euo pipefail

cd /Users/supremeleader/Desktop/Arbitrage

SESSION="arb_ws_orderbook"
ARB_WS_SPORTS="${ARB_WS_SPORTS:-tennis,esports,baseball}"

mkdir -p data/new
chmod +x scripts/new_run_ws_orderbook_supervisor.sh

if [[ $# -ge 1 ]]; then
  printf "%s" "$1" > data/new/new_kalshi_api_key_id
  chmod 600 data/new/new_kalshi_api_key_id
elif [[ -n "${KALSHI_API_KEY_ID:-}" ]]; then
  printf "%s" "$KALSHI_API_KEY_ID" > data/new/new_kalshi_api_key_id
  chmod 600 data/new/new_kalshi_api_key_id
elif [[ ! -f data/new/new_kalshi_api_key_id ]]; then
  echo "Usage: scripts/new_start_ws_orderbook_tmux.sh <KALSHI_API_KEY_ID>" >&2
  echo "Or set KALSHI_API_KEY_ID before running." >&2
  exit 1
fi

tmux has-session -t "$SESSION" 2>/dev/null && tmux kill-session -t "$SESSION"
tmux new-session -d -s "$SESSION" "cd /Users/supremeleader/Desktop/Arbitrage && ./scripts/new_run_ws_orderbook_supervisor.sh >> data/new/new_ws_orderbook_tmux.log 2>&1"

echo "Started tmux session: $SESSION"
echo "Sports: $ARB_WS_SPORTS"
echo "Log: data/new/new_ws_orderbook_tmux.log"
echo "Status CSV: data/new/new_latest_ws_status.csv"
