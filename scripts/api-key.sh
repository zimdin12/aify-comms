#!/bin/bash
# The key that opens this service, read from where the service itself reads it.
#
#   bash scripts/api-key.sh              # print the key, or nothing
#   bash scripts/api-key.sh --generate   # print it, creating and persisting one if there is none
#
# THE INSTALLER NEVER LOOKED IN `.env`, and four call sites each typed the same two-name precedence.
# That is not untidiness, it is a trap with a delay on it. The service installs its auth middleware
# only when `API_KEY` is set, so today everything works keyless. The moment an operator sets it, the
# service starts refusing unauthenticated calls, every installed client holds no key, and re-running
# the installer does NOT fix them: it looked only in the environment, found nothing, and wrote the
# same keyless config again. The obvious remedy makes no difference, which is the worst shape a
# failure can have.
#
# So the resolution order ends at the file the service reads:
#
#   1. CLAUDE_MCP_API_KEY   an explicit choice in this shell, which always wins
#   2. AIFY_API_KEY         the same, under the other name the bridge reads
#   3. API_KEY in .env      what the SERVICE is actually configured with
#
# NEVER ROTATED. A key found anywhere above is reused verbatim: minting a fresh one would leave every
# already-installed bridge holding the old value, which is the same outage as the one this fixes,
# caused by the fix for it.
#
# A SEPARATE SCRIPT, like scripts/installed-endpoint.sh and scripts/hook-installed.sh, and for the
# same reason they are: this reads what the host already chose, before an update overwrites it.
# Keeping it out of install.sh also keeps that file under the ceiling its ratchet holds.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

read_existing_key() {
  if [ -n "${CLAUDE_MCP_API_KEY:-}" ]; then printf '%s\n' "$CLAUDE_MCP_API_KEY"; return 0; fi
  if [ -n "${AIFY_API_KEY:-}" ]; then printf '%s\n' "$AIFY_API_KEY"; return 0; fi
  [ -f "$ENV_FILE" ] || return 0

  # READ, NEVER SOURCED. `.env` is operator-edited, and a stray backtick or `$(...)` in any line of it
  # would EXECUTE while this runs as the operator. The value is taken literally; surrounding quotes are
  # stripped because both spellings are common, and a key carrying its own quotes matches nothing --
  # a 401 whose cause is invisible in every log on both sides.
  local line value
  line="$(grep -m1 '^[[:space:]]*API_KEY[[:space:]]*=' "$ENV_FILE" 2>/dev/null || true)"
  [ -n "$line" ] || return 0
  value="${line#*=}"
  value="${value%\"}"; value="${value#\"}"
  value="${value%\'}"; value="${value#\'}"
  value="$(printf '%s' "$value" | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//')"
  [ -n "$value" ] && printf '%s\n' "$value"
  return 0
}

generate_key() {
  local existing
  existing="$(read_existing_key)"
  if [ -n "$existing" ]; then printf '%s\n' "$existing"; return 0; fi

  local key=""
  if command -v openssl >/dev/null 2>&1; then
    key="$(openssl rand -hex 32)"
  elif [ -r /dev/urandom ]; then
    key="$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  elif command -v node >/dev/null 2>&1; then
    key="$(node -e 'process.stdout.write(require("node:crypto").randomBytes(32).toString("hex"))')"
  fi
  # FAILS CLOSED. A weak key is worse than none: it reads as protection while being guessable, and
  # every caller would then be configured to trust it.
  if [ ${#key} -lt 32 ]; then
    echo "ERROR: could not generate an API key (no openssl, /dev/urandom or node)." >&2
    return 1
  fi

  [ -f "$ENV_FILE" ] || touch "$ENV_FILE"
  printf 'API_KEY=%s\n' "$key" >> "$ENV_FILE"
  printf '%s\n' "$key"
}

if [ "${1:-}" = "--generate" ]; then
  generate_key
else
  read_existing_key
fi
