#!/usr/bin/env bash
set -euo pipefail

PLIST="$HOME/Library/LaunchAgents/com.arbitrage.ws-orderbook.plist"

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
echo "Stopped com.arbitrage.ws-orderbook"
