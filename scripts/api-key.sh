#!/bin/bash
# The key that opens this service, read from where the SERVICE itself reads it.
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
#   1. CLAUDE_MCP_API_KEY   an explicit choice in this shell
#   2. AIFY_API_KEY         the same, under the other name the bridge reads
#   3. API_KEY in .env      what the SERVICE is actually configured with
#
# THE LAST DEFINITION WINS INSIDE `.env`, BECAUSE THAT IS WHAT COMPOSE DOES. This script used to take
# the FIRST (`grep -m1`) and a test asserted that as correct, "the way a dotenv reader reads it".
# Some dotenv libraries do read first; the consumer here is not one. `docker-compose.yml` passes
# `.env` as `env_file`, and Compose parses it into a map where a later line overwrites an earlier
# one. MEASURED, both ways, against real Compose: on `API_KEY=FIRST_aaa` then `API_KEY=LAST_bbb`,
# `docker compose config` renders `LAST_bbb`, and swapping the two lines swaps the answer. So a
# duplicated key handed the SERVICE one value and every CLIENT the other -- a 401 on every call with
# both halves looking correctly configured. `--generate` now REPLACES rather than appends, so this
# script cannot author the duplicate it used to misread.
#
# NEVER ROTATED. A key found anywhere above is reused verbatim: minting a fresh one would leave every
# already-installed bridge holding the old value, which is the same outage as the one this fixes,
# caused by the fix for it.
#
# ABSENT AND ERROR ARE DIFFERENT ANSWERS, and the caller must be able to tell them apart:
#
#   exit 0, a key on stdout    resolved
#   exit 0, nothing on stdout  ABSENT -- no key is configured anywhere, which is a valid deployment
#   exit 3                     CONFLICT -- the shell and `.env` name DIFFERENT keys
#   exit 1                     ERROR -- could not read, or could not generate
#
# HOW THE CALLERS MUST READ THIS. `install.sh` wrapped this in `|| true`, which kept "no key"
# non-fatal -- a supported configuration -- but turned a real failure into the same answer, so a
# keyless config got written after an ERROR and looked exactly like a host that had never set one.
# The `|| true` is gone. ABSENT still flows through harmlessly; a CONFLICT or an unreadable file now
# stops the install rather than configuring every client with a value the service will refuse.
#
# That abort works only because of how the call sites are written, and the difference is one
# semicolon. All six say `local api_key; api_key="$(aify_api_key)"`. MEASURED under `set -e`: the
# split form propagates the substitution's exit status and the installer stops (exit 3), while the
# one-line `local api_key="$(aify_api_key)"` exits 0 and hands back an empty string -- `local` is
# itself a command, and its own success is what bash sees. Keep the declaration on its own line.
#
# A SEPARATE SCRIPT, like scripts/installed-endpoint.sh and scripts/hook-installed.sh, and for the
# same reason they are: this reads what the host already chose, before an update overwrites it.
# Keeping it out of install.sh also keeps that file under the ceiling its ratchet holds.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env"

#: The floor a key must clear, named ONCE. The generator held itself to this and nothing checked a
#: key that arrived any other way, so an operator's weak key was reused as though it had been vetted.
MIN_KEY_LENGTH=32

#: Strip the shell quoting an operator commonly writes, plus CR from a file edited on Windows. A key
#: carrying its own quotes matches nothing: a 401 whose cause is invisible in every log on both sides.
unquote_value() {
  local value="$1"
  value="${value%\"}"; value="${value#\"}"
  value="${value%\'}"; value="${value#\'}"
  printf '%s' "$value" | tr -d '\r' | sed 's/^[[:space:]]*//; s/[[:space:]]*$//'
}

#: The value the SERVICE will see: the LAST definition, the way Compose resolves an env_file.
#: READ, NEVER SOURCED. `.env` is operator-edited, and a stray backtick or `$(...)` in any line of it
#: would EXECUTE while this runs as the operator. The value is taken literally.
key_from_env_file() {
  [ -f "$ENV_FILE" ] || return 0
  local line
  line="$(grep '^[[:space:]]*API_KEY[[:space:]]*=' "$ENV_FILE" 2>/dev/null | tail -n1 || true)"
  [ -n "$line" ] || return 0
  unquote_value "${line#*=}"
}

key_from_shell() {
  if [ -n "${CLAUDE_MCP_API_KEY:-}" ]; then unquote_value "$CLAUDE_MCP_API_KEY"; return 0; fi
  if [ -n "${AIFY_API_KEY:-}" ]; then unquote_value "$AIFY_API_KEY"; return 0; fi
  return 0
}

#: Applied to EVERY non-empty key whatever its source, which is the half that was missing. A weak key
#: that is ALREADY IN USE is reported rather than refused: the service is running on it, every
#: installed bridge holds it, and aborting the install neither rotates it nor helps the operator. It
#: is still a real finding, so it goes to stderr where an install log keeps it.
warn_if_weak() {
  local key="$1" source="$2"
  [ -n "$key" ] || return 0
  if [ ${#key} -lt $MIN_KEY_LENGTH ]; then
    echo "WARNING: the API key from $source is ${#key} characters; $MIN_KEY_LENGTH is the floor this" >&2
    echo "         project generates to. It reads as protection while being guessable. Rotating it" >&2
    echo "         means re-running install.sh for every client, so it is reported and not refused." >&2
  fi
}

#: Resolve ONCE, and let a disagreement be an answer rather than a silent preference. A shell key and
#: a different `.env` key is the case that configures every client with a value the service will
#: refuse: the clients get the shell key, the service restarts onto the file key, and all of it looks
#: correctly installed. There is a right action available, so this refuses and names it.
resolve_key() {
  local shell_key file_key
  shell_key="$(key_from_shell)"
  file_key="$(key_from_env_file)"

  if [ -n "$shell_key" ] && [ -n "$file_key" ] && [ "$shell_key" != "$file_key" ]; then
    echo "ERROR: this shell and $ENV_FILE name DIFFERENT API keys." >&2
    echo "       Clients would be configured with the shell's key and the service would run on the" >&2
    echo "       file's, so every call would 401 with both halves looking correct." >&2
    echo "       Unset CLAUDE_MCP_API_KEY / AIFY_API_KEY to use the service's own key, or set" >&2
    echo "       API_KEY in $ENV_FILE to match the shell." >&2
    return 3
  fi

  if [ -n "$shell_key" ]; then
    warn_if_weak "$shell_key" "this shell"
    printf '%s\n' "$shell_key"
    return 0
  fi
  if [ -n "$file_key" ]; then
    warn_if_weak "$file_key" "$ENV_FILE"
    printf '%s\n' "$file_key"
    return 0
  fi
  return 0
}

#: Write `.env` with exactly ONE API_KEY line, atomically. Replacing rather than appending is what
#: stops this script authoring the duplicate whose two readers disagree; writing a temp file and
#: moving it is what stops an interrupted install leaving a truncated `.env` and taking the service's
#: whole configuration with it.
persist_key() {
  local key="$1" tmp
  [ -f "$ENV_FILE" ] || touch "$ENV_FILE"
  tmp="$(mktemp "$ENV_FILE.XXXXXX")"
  grep -v '^[[:space:]]*API_KEY[[:space:]]*=' "$ENV_FILE" > "$tmp" || true
  printf 'API_KEY=%s\n' "$key" >> "$tmp"
  mv -f "$tmp" "$ENV_FILE"
}

generate_key() {
  local existing
  existing="$(resolve_key)" || return $?
  if [ -n "$existing" ]; then
    # PERSIST WHAT WE HAND OUT. A shell-only key used to be exported into every client and never
    # written where the service reads it, so the next service restart came up keyless or on another
    # key while every client presented this one.
    if [ "$existing" != "$(key_from_env_file)" ]; then persist_key "$existing"; fi
    printf '%s\n' "$existing"
    return 0
  fi

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
  if [ ${#key} -lt $MIN_KEY_LENGTH ]; then
    echo "ERROR: could not generate an API key (no openssl, /dev/urandom or node)." >&2
    return 1
  fi

  persist_key "$key"
  printf '%s\n' "$key"
}

if [ "${1:-}" = "--generate" ]; then
  generate_key
else
  resolve_key
fi
