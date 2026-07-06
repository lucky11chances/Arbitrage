#!/usr/bin/env bash
set -euo pipefail

tmux kill-session -t arb_ws_orderbook 2>/dev/null || true
echo "Stopped tmux session: arb_ws_orderbook"
