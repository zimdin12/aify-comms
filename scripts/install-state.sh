#!/bin/bash
# Read this host before proposing an install/update. No installers or daemons are started.
# bash scripts/install-state.sh [--json]
# Hook roots use the installer's resolver; failures are UNKNOWN, never absence.
# Credential values stay in memory, never in the report. herdr discovery is passive;
# use scripts/herdr-state.sh --probe separately for its bounded --version check.
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN_DIR="${AIFY_BIN_DIR:-$HOME/.local/bin}"
source "$REPO_ROOT/scripts/hermes-config.sh"
source "$REPO_ROOT/scripts/report-json.sh"
JSON=false
case "${1:-}" in
  --json) JSON=true ;;
  "") ;;
  *) echo "usage: install-state.sh [--json]" >&2; exit 2 ;;
esac

endpoint_installed="$(bash "$REPO_ROOT/scripts/installed-endpoint.sh" "$BIN_DIR" 2>/dev/null)"
endpoint_status=$?
service_url="${endpoint_installed:-http://localhost:8800}"
service_health=unknown
if command -v curl >/dev/null 2>&1; then
  curl -fsS --max-time 3 "$service_url/health" >/dev/null 2>&1
  case $? in
    0) service_health=healthy ;;
    7) service_health=unreachable ;;
    *) service_health=unknown ;;
  esac
fi

# THE FILTER IS A SUBSTRING AND THE SHELL DOES THE EXACT MATCH, deliberately. This asked docker for
# `name=^/aify-comms-service$` -- the leading slash is the old container-name convention -- and on
# Docker 29 that anchored form matches NOTHING, so this reported the service container ABSENT while
# it was up and serving, with `health: healthy` printed on the same line. An inventory that denies a
# running container is the state-that-lies shape this repo exists to catch, and it was invisible
# because the only test of this block stubs docker away entirely and asserts `unknown`.
#
# Anchoring is a docker-version dialect; `grep -Fx` is not. Filtering loosely and comparing exactly
# here is correct on both old and new daemons and stops this from turning on a flag we do not own.
container=unknown
service_container=aify-comms-service
if command -v docker >/dev/null 2>&1; then
  if running="$(docker ps --filter "name=$service_container" --format '{{.Names}}' 2>/dev/null | grep -Fx "$service_container")" && [ -n "$running" ]; then
    container=running
  elif all="$(docker ps -a --filter "name=$service_container" --format '{{.Names}}' 2>/dev/null)"; then
    container=absent
    printf '%s\n' "$all" | grep -Fxq "$service_container" && container=stopped
  fi
fi

api_key=unknown
if key="$(bash "$REPO_ROOT/scripts/api-key.sh" 2>/dev/null)"; then
  api_key=none
  [ -z "$key" ] || api_key=configured
fi
unset key

installed_clients=""
for candidate in "$BIN_DIR"/*-aify; do
  [ -f "$candidate" ] || continue
  grep -qE '^[[:space:]]*HARNESS_WRAPPER_VERSION[[:space:]]*=' "$candidate" 2>/dev/null || continue
  name="$(basename "$candidate")"
  installed_clients="${installed_clients:+$installed_clients }${name%-aify}"
done

hooks="" hooks_unknown="" hook_states=""
hermes_root="$(hermes_config_root --require-resolved 2>/dev/null)"
hermes_root_status=$?
for client in claude codex hermes; do
  root="" status=2
  if [ "$client" != hermes ] || [ "$hermes_root_status" -eq 0 ]; then
    [ "$client" != hermes ] || root="$hermes_root"
    bash "$REPO_ROOT/scripts/hook-installed.sh" "$client" "$root" >/dev/null 2>&1
    status=$?
  fi
  case "$status" in
    0) state=installed; hooks="${hooks:+$hooks }$client" ;;
    1) state=absent ;;
    *) state=unknown; hooks_unknown="${hooks_unknown:+$hooks_unknown }$client" ;;
  esac
  hook_states="${hook_states:+$hook_states,}\"$client\":\"$state\""
done

# A missing PATH command plus a failed health query cannot prove absence.
aify_env=unknown
command -v aify-env >/dev/null 2>&1 && aify_env=installed
if command -v curl >/dev/null 2>&1; then
  curl -fsS --max-time 2 "http://127.0.0.1:${AIFY_ENV_PORT:-8802}/health" >/dev/null 2>&1 && aify_env=running
fi
registry="${AIFY_SERVICE_REGISTRY:-$HOME/.aify/services.json}"
registered=no
if [ -e "$registry" ] || [ -L "$registry" ]; then
  registered=unknown
  if [ -r "$registry" ] && command -v node >/dev/null 2>&1; then
    reader="$REPO_ROOT/scripts/registry-state.mjs"
    native_registry="$registry"
    if command -v cygpath >/dev/null 2>&1; then
      reader="$(cygpath -m "$reader")"
      native_registry="$(cygpath -m "$registry")"
    fi
    result="$(node "$reader" "$native_registry" 2>/dev/null)" || result=unknown
    case "$result" in yes|no) registered="$result" ;; esac
  fi
fi
bridge_copy=absent
[ -d "${AIFY_HOME:-$HOME/.aify-comms}" ] && bridge_copy=present
herdr="$(bash "$REPO_ROOT/scripts/herdr-state.sh")" || exit 2

if [ "$JSON" = true ]; then
  printf '{'
  for field in container serviceUrl serviceHealth apiKey installedClients hooks hooksUnknown aifyEnv registeredInRegistry bridgeCopy endpointInstalled; do
    case "$field" in
      container) value="$container" ;; serviceUrl) value="$service_url" ;;
      serviceHealth) value="$service_health" ;; apiKey) value="$api_key" ;;
      installedClients) value="$installed_clients" ;; hooks) value="$hooks" ;;
      hooksUnknown) value="$hooks_unknown" ;; aifyEnv) value="$aify_env" ;;
      registeredInRegistry) value="$registered" ;; bridgeCopy) value="$bridge_copy" ;;
      endpointInstalled) value="$endpoint_installed" ;;
    esac
    printf '"%s":' "$field"; json_string "$value"; printf ','
  done
  printf '"endpointProbeExit":%s,"hookStates":{%s},"herdr":%s}\n' "$endpoint_status" "$hook_states" "$herdr"
  exit 0
fi

printf 'aify-comms installation inventory\n\n'
printf '  Service container: %s; health: %s\n' "$container" "$service_health"
printf '  Endpoint: %s (reader exit %s)\n' "$service_url" "$endpoint_status"
printf '  API key: %s\n' "$api_key"
printf '  Launchers: %s\n' "${installed_clients:-(none found)}"
printf '  Hooks installed: %s; UNKNOWN: %s\n' "${hooks:-(none)}" "${hooks_unknown:-(none)}"
printf '  Bridge copy: %s; aify-env: %s; registry entry: %s\n' "$bridge_copy" "$aify_env" "$registered"
printf '  Optional herdr: %s\n' "$herdr"
printf '\nInventory is not version or running-identity verification. Follow the install skill before changes.\n'
