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

# Stamp the build SHA into service/_build_stamp.json from the host git checkout
# BEFORE any container rebuild (the container has no .git of its own). Safe and
# non-fatal: stamp.sh falls back to "unknown" outside a checkout.
# NOTE: this redeploy.sh refreshes the host *-aify wrappers only — it does NOT
# itself run `docker compose up -d --build`. The container rebuild is the
# documented `docker compose up -d --build` step (see CLAUDE.md); stamp.sh is
# stamped here so the file is fresh whenever the operator rebuilds next.
if [ -f "$REPO_ROOT/scripts/stamp.sh" ]; then
  bash "$REPO_ROOT/scripts/stamp.sh" || echo "redeploy.sh: stamp.sh failed (non-fatal)" >&2
fi

WRAPPERS_DIR="$HOME/.local/bin"
if [ ! -d "$WRAPPERS_DIR" ]; then
  echo "redeploy.sh: $WRAPPERS_DIR does not exist; nothing to redeploy" >&2
  exit 0
fi

CLIENTS=()
# Pi/OMP is intentionally not redeployed as a resident wrapper. Triggerable
# Pi delivery uses the managed persistent `omp --mode rpc` controller, not
# `omp-aify` / `pi-aify`.
# OpenCode wrapper/config install is also disabled until that integration gets
# the same focused resident/managed validation as Claude, Codex, and Hermes.
for client in claude codex hermes; do
  if [ -x "$WRAPPERS_DIR/${client}-aify" ] || [ -x "$WRAPPERS_DIR/${client}-aify.cmd" ]; then
    CLIENTS+=("$client")
  fi
done

client_runtime_available() {
  local client="$1"
  case "$client" in
    claude)
      command -v claude >/dev/null 2>&1
      ;;
    codex)
      command -v codex >/dev/null 2>&1
      ;;
    hermes)
      [ -n "${AIFY_HERMES_COMMAND:-${HERMES_COMMAND:-}}" ] || command -v hermes >/dev/null 2>&1
      ;;
    *)
      return 1
      ;;
  esac
}

if [ ${#CLIENTS[@]} -eq 0 ]; then
  echo "redeploy.sh: no *-aify wrappers detected in $WRAPPERS_DIR"
  exit 0
fi

echo "redeploy.sh: detected wrappers: ${CLIENTS[*]}"
echo "redeploy.sh: server URL: $SERVER_URL"

FAILED=()
SKIPPED=()
REFRESHED=0
for client in "${CLIENTS[@]}"; do
  echo
  if ! client_runtime_available "$client"; then
    echo "redeploy.sh: skipping $client; runtime command is not available in this shell"
    SKIPPED+=("$client")
    continue
  fi
  echo "redeploy.sh: refreshing $client..."
  if bash "$REPO_ROOT/install.sh" --client "$client" "$SERVER_URL"; then
    echo "redeploy.sh: $client refreshed"
    REFRESHED=$((REFRESHED + 1))
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

echo "redeploy.sh: wrappers refreshed: $REFRESHED"
if [ ${#SKIPPED[@]} -gt 0 ]; then
  echo "redeploy.sh: skipped unavailable runtimes: ${SKIPPED[*]}"
fi
echo
echo "Reminder: restart any open *-aify sessions to pick up the new wrappers."
