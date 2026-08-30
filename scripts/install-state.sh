#!/bin/bash
# What is already installed on this machine, so an installer only asks about the gaps.
#
#   bash scripts/install-state.sh          # human-readable
#   bash scripts/install-state.sh --json   # one JSON object
#
# WHY THIS EXISTS. Installing aify-comms meant reading 1,227 lines of per-runtime guides and knowing
# which half applied to this machine. Most of that is not a decision at all -- it is a question about
# the host, and the host can be asked. What is left is small enough to put to a person.
#
# READS, NEVER RUNS. Every answer here comes from a file, a port, or a process listing. Asking a
# launcher what it is by executing it starts a coding-agent runtime, which is how a fleet went down;
# `scripts/installed-endpoint.sh`, `scripts/hook-installed.sh` and `scripts/api-key.sh` exist for the
# same reason and this calls them rather than re-deriving their answers.
#
# NEVER PRINTS THE KEY. It prints whether one is configured. A state report that leaks a credential
# into a terminal, a log, and an agent's context is a worse problem than the one it solves.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BIN_DIR="${AIFY_BIN_DIR:-$HOME/.local/bin}"
JSON=false
[ "${1:-}" = "--json" ] && JSON=true

# --- the service -------------------------------------------------------------------------------
endpoint_installed="$(bash "$REPO_ROOT/scripts/installed-endpoint.sh" "$BIN_DIR" 2>/dev/null || true)"
service_url="${endpoint_installed:-http://localhost:8800}"
service_health="unreachable"
if command -v curl >/dev/null 2>&1; then
  if curl -fsS --max-time 3 "$service_url/health" >/dev/null 2>&1; then service_health="healthy"; fi
fi

container="absent"
if command -v docker >/dev/null 2>&1; then
  if docker ps --filter name=aify-comms-service --format '{{.Names}}' 2>/dev/null | grep -q .; then
    container="running"
  elif docker ps -a --filter name=aify-comms-service --format '{{.Names}}' 2>/dev/null | grep -q .; then
    container="stopped"
  fi
fi

# --- the key -----------------------------------------------------------------------------------
# Whether, never what.
if [ -n "$(bash "$REPO_ROOT/scripts/api-key.sh" 2>/dev/null || true)" ]; then
  api_key="configured"
else
  api_key="none"
fi

# --- the clients -------------------------------------------------------------------------------
# A launcher counts when it carries the harness contract marker, the same rule aify-env uses to
# decide what it may execute. A file merely NAMED like a launcher is not one.
installed_clients=""
for candidate in "$BIN_DIR"/*-aify; do
  [ -f "$candidate" ] || continue
  grep -qE '^[[:space:]]*HARNESS_WRAPPER_VERSION[[:space:]]*=' "$candidate" 2>/dev/null || continue
  name="$(basename "$candidate")"
  installed_clients="${installed_clients:+$installed_clients }${name%-aify}"
done

hooks=""
for client in claude codex hermes; do
  if bash "$REPO_ROOT/scripts/hook-installed.sh" "$client" >/dev/null 2>&1; then
    hooks="${hooks:+$hooks }$client"
  fi
done

# --- the environment tier ------------------------------------------------------------------------
aify_env="absent"
command -v aify-env >/dev/null 2>&1 && aify_env="installed"
if command -v curl >/dev/null 2>&1; then
  if curl -fsS --max-time 2 "http://127.0.0.1:${AIFY_ENV_PORT:-8802}/health" >/dev/null 2>&1; then
    aify_env="running"
  fi
fi

registry="${AIFY_SERVICE_REGISTRY:-$HOME/.aify/services.json}"
registered="no"
[ -f "$registry" ] && grep -q '"aify-comms"' "$registry" 2>/dev/null && registered="yes"

bridge_copy="absent"
[ -d "${AIFY_HOME:-$HOME/.aify-comms}" ] && bridge_copy="present"

if [ "$JSON" = true ]; then
  printf '{'
  printf '"container":"%s",' "$container"
  printf '"serviceUrl":"%s",' "$service_url"
  printf '"serviceHealth":"%s",' "$service_health"
  printf '"apiKey":"%s",' "$api_key"
  printf '"installedClients":"%s",' "$installed_clients"
  printf '"hooks":"%s",' "$hooks"
  printf '"aifyEnv":"%s",' "$aify_env"
  printf '"registeredInRegistry":"%s",' "$registered"
  printf '"bridgeCopy":"%s",' "$bridge_copy"
  printf '"endpointInstalled":"%s"' "$endpoint_installed"
  printf '}\n'
  exit 0
fi

echo "aify-comms — what this machine already has"
echo
echo "  SERVICE"
echo "    container ......... $container"
echo "    endpoint .......... ${endpoint_installed:-(none installed; default http://localhost:8800)}"
echo "    reachable ......... $service_health"
echo "    API key ........... $api_key"
echo
echo "  THIS MACHINE AS AN AGENT HOST"
echo "    launchers ......... ${installed_clients:-(none)}"
echo "    notify hooks ...... ${hooks:-(none)}"
echo "    bridge copy ....... $bridge_copy  (${AIFY_HOME:-$HOME/.aify-comms})"
echo "    aify-env .......... $aify_env"
echo "    in services.json .. $registered"
echo
echo "Next: bash install.sh --client <claude|codex|hermes> <endpoint> [--with-hook] [--with-api-key]"
