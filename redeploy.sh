#!/bin/bash
# redeploy.sh — Plan 4 helper. Detects which *-aify wrappers are installed
# at ~/.local/bin/ and reinvokes install.sh --client <X> SERVER_URL for
# each detected wrapper.
#
# Usage:
#   ./redeploy.sh                                  # uses default server URL
#   ./redeploy.sh http://192.168.100.10:8800       # explicit URL
#
# Use after pulling new aify-comms changes to refresh all installed
# *-aify wrappers without manually running install.sh per-client.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
DEFAULT_SERVER="${AIFY_DEFAULT_SERVER_URL:-http://192.168.100.10:8800}"
SERVER_URL="${1:-$DEFAULT_SERVER}"

if [ ! -f "$REPO_ROOT/install.sh" ]; then
  echo "redeploy.sh: install.sh not found at $REPO_ROOT" >&2
  exit 1
fi

WRAPPERS_DIR="$HOME/.local/bin"
if [ ! -d "$WRAPPERS_DIR" ]; then
  echo "redeploy.sh: $WRAPPERS_DIR does not exist; nothing to redeploy" >&2
  exit 0
fi

CLIENTS=()
for client in claude codex hermes pi opencode; do
  if [ -x "$WRAPPERS_DIR/${client}-aify" ] || [ -x "$WRAPPERS_DIR/${client}-aify.cmd" ]; then
    CLIENTS+=("$client")
  fi
done

if [ ${#CLIENTS[@]} -eq 0 ]; then
  echo "redeploy.sh: no *-aify wrappers detected in $WRAPPERS_DIR"
  exit 0
fi

echo "redeploy.sh: detected wrappers: ${CLIENTS[*]}"
echo "redeploy.sh: server URL: $SERVER_URL"

FAILED=()
for client in "${CLIENTS[@]}"; do
  echo
  echo "redeploy.sh: refreshing $client..."
  if bash "$REPO_ROOT/install.sh" --client "$client" "$SERVER_URL"; then
    echo "redeploy.sh: $client refreshed"
  else
    echo "redeploy.sh: install.sh failed for $client" >&2
    FAILED+=("$client")
  fi
done

echo
if [ ${#FAILED[@]} -gt 0 ]; then
  echo "redeploy.sh: failures: ${FAILED[*]}" >&2
  exit 1
fi

echo "redeploy.sh: all wrappers refreshed (${#CLIENTS[@]})."
echo
echo "Reminder: restart any open *-aify sessions to pick up the new wrappers."
