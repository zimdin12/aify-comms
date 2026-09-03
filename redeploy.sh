#!/bin/bash
# redeploy.sh — Plan 4 helper. Detects which *-aify wrappers are installed
# at ~/.local/bin/ and reinvokes install.sh --client <X> SERVER_URL for
# each detected wrapper.
#
# Usage:
#   ./redeploy.sh                                  # uses default server URL
#   ./redeploy.sh http://my-server:8800             # explicit URL
#
# Use after pulling new aify-comms changes to refresh all installed
# *-aify wrappers without manually running install.sh per-client.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"

# WHAT THE HOST LOOKED LIKE BEFORE THIS UPDATE, captured first so the end of this script can say
# what the update actually DID rather than what it attempted. CLAUDE.md opens with the reason:
# every deploy path in this repo fails silently, and an update that reports "refreshed" is making a
# claim about its own intentions. Best-effort throughout -- a host with no verifier yet gets an
# empty baseline and is told the update is unverified, which is the honest answer rather than a
# reason to refuse to update.
DEPLOY_DELTA_DIR="$(mktemp -d 2>/dev/null || echo "")"
if [ -n "$DEPLOY_DELTA_DIR" ]; then
  trap 'rm -rf "$DEPLOY_DELTA_DIR"' EXIT
  bash "$REPO_ROOT/scripts/deploy-delta.sh" capture "$DEPLOY_DELTA_DIR/before" 2>/dev/null || true
fi
# Server URL precedence: explicit $1 > $AIFY_DEFAULT_SERVER_URL > the URL the installed
# wrappers were last built with > loopback. Never a baked-in LAN address: that only ever
# worked on one machine, and shipping it in a public repo published that host to everyone.
detect_installed_server_url() {
  # The wrappers bake their server URL at install time; reuse it so a re-deploy keeps pointing at
  # whatever the operator actually chose. scripts/installed-endpoint.sh is the one reader -- this
  # function used to hold its own copy of a regex that matched the PRE-CONTRACT wrapper shape, so it
  # quietly stopped finding anything and this script fell through to loopback, which would have
  # rewritten every wrapper on the host to point at 127.0.0.1.
  bash "$REPO_ROOT/scripts/installed-endpoint.sh" "$HOME/.local/bin" 2>/dev/null
}
DEFAULT_SERVER="${AIFY_DEFAULT_SERVER_URL:-$(detect_installed_server_url || echo "http://127.0.0.1:8800")}"
SERVER_URL="${1:-$DEFAULT_SERVER}"

# WHERE SPAWNS GO, carried across the update the same way the endpoint is. install.sh bakes delegation
# only when asked, so re-rendering without asking moved managed spawns back off aify-env -- observed
# minutes after the flip, `spawn-delegation` going from `delegated` to `local` across a routine
# redeploy whose whole promise is that it changes nothing but the code.
#
# Empty when delegation is off, which reproduces an un-delegated install exactly.
DELEGATE_ARGS=()
if _installed_env_endpoint="$(bash "$REPO_ROOT/scripts/installed-delegation.sh" "$HOME/.local/bin" 2>/dev/null)"; then
  DELEGATE_ARGS=(--delegate-spawns "$_installed_env_endpoint")
  echo "redeploy.sh: keeping managed spawns delegated to $_installed_env_endpoint"
fi

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
  if bash "$REPO_ROOT/install.sh" --client "$client" "$SERVER_URL" "${DELEGATE_ARGS[@]+"${DELEGATE_ARGS[@]}"}"; then
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

# AND DID IT TAKE EFFECT. The verifier proves each claim against the RUNNING system, so comparing
# its verdicts across the update is the difference between an update that landed and one that
# reported success and changed nothing -- which is this repo's most expensive recurring failure.
# Nothing here names a check: the comparison is over the whole verdict set, so a check added later
# is covered on the day it lands.
if [ -n "${DEPLOY_DELTA_DIR:-}" ]; then
  echo
  echo "redeploy.sh: what this update changed --"
  bash "$REPO_ROOT/scripts/deploy-delta.sh" capture "$DEPLOY_DELTA_DIR/after" 2>/dev/null || true
  # NON-FATAL BY DESIGN. The wrappers ARE refreshed by this point; exiting non-zero here would
  # report the update as failed when what actually happened is that it succeeded and broke
  # something. Say which, loudly, and let the operator decide -- a regression they can see beats an
  # exit code they have to interpret.
  bash "$REPO_ROOT/scripts/deploy-delta.sh" compare "$DEPLOY_DELTA_DIR/before" "$DEPLOY_DELTA_DIR/after" || true
fi
