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
# NEVER ROTATED. A key found anywhere above is reused verbatim: minting a fresh one would leave every
# already-installed bridge holding the old value, which is the same outage as the one this fixes,
# caused by the fix for it.
#
# ---------------------------------------------------------------------------------------------
# THE GRAMMAR THIS SUPPORTS, MEASURED AGAINST REAL COMPOSE (2026-08-31, `docker compose config` on a
# throwaway project). Guessing here is how the installer and the service end up holding different
# keys, so each rule below is an observation, not a belief:
#
#   API_KEY=plain            -> plain          a bare value
#   API_KEY="dq"             -> dq             double quotes are stripped
#   API_KEY='sq'             -> sq             single quotes are stripped
#   export API_KEY=exported  -> exported       an `export` prefix is accepted
#      API_KEY=lead          -> lead           leading whitespace (space or tab) is ignored
#   API_KEY = spaced         -> spaced         whitespace may surround the `=`
#   API_KEY=trail            -> trail          trailing whitespace is trimmed
#   API_KEY=val #comment     -> val            `#` starts a comment ONLY after whitespace
#   API_KEY=val#nospace      -> val#nospace    ...so an un-spaced `#` is part of the value
#   API_KEY="val #keep"      -> val #keep      quotes protect it
#   #API_KEY=nope            -> (absent)       a commented line is not a definition
#   API_KEY=                 -> ""             a declared empty value is not a key
#
# THE LAST DEFINITION WINS. This script used to take the FIRST (`grep -m1`) and a test asserted that
# as correct, "the way a dotenv reader reads it". Some dotenv libraries do read first; the consumer
# here is not one. `docker-compose.yml` passes `.env` as `env_file`, and Compose parses it into a map
# where a later line overwrites an earlier one. Measured both ways: on `API_KEY=FIRST_aaa` then
# `API_KEY=LAST_bbb`, Compose renders `LAST_bbb`, and swapping the lines swaps the answer. So a
# duplicated key handed the SERVICE one value and every CLIENT the other. `--generate` REPLACES
# rather than appends, so this script cannot author the duplicate it used to misread.
#
# WHAT IT REFUSES RATHER THAN GUESSES. Compose processes backslash escapes inside quotes (`"a\"b"`
# becomes `a"b`, and `\n` becomes a real newline in BOTH quote styles). Reimplementing that here
# would be a second parser to keep in step with theirs, and a wrong answer is a key mismatch nobody
# can see. A quoted value containing a backslash is reported as UNSUPPORTED (exit 5) so the operator
# is told, instead of being handed a value the service will not agree with.
#
# ---------------------------------------------------------------------------------------------
# ABSENT AND ERROR ARE DIFFERENT ANSWERS, and every caller must be able to tell them apart:
#
#   exit 0, a key on stdout    resolved
#   exit 0, nothing on stdout  ABSENT -- no key is configured anywhere, which is a valid deployment
#   exit 1                     ERROR -- `.env` could not be read, or a key could not be generated
#   exit 3                     CONFLICT -- the shell and `.env` name DIFFERENT keys
#   exit 4                     WEAK -- `--generate` was asked to adopt a key below the floor
#   exit 5                     UNSUPPORTED -- a `.env` value this cannot parse the way Compose does
#
# HOW THE CALLERS MUST READ THIS. `install.sh` wrapped this in `|| true`, which kept "no key"
# non-fatal -- a supported configuration -- but turned a real failure into the same answer, so a
# keyless config got written after an ERROR and looked exactly like a host that had never set one.
# The `|| true` is gone. ABSENT still flows through harmlessly; every other non-zero stops the
# install rather than configuring every client with a value the service will refuse.
#
# That abort works only because of how the call sites are written, and the difference is one
# semicolon. All six say `local api_key; api_key="$(aify_api_key)"`. MEASURED under `set -e`: the
# split form propagates the substitution's exit status and the installer stops, while the one-line
# `local api_key="$(aify_api_key)"` exits 0 and hands back an empty string -- `local` is itself a
# command, and its own success is what bash sees. Keep the declaration on its own line.
#
# A SEPARATE SCRIPT, like scripts/installed-endpoint.sh and scripts/hook-installed.sh, and for the
# same reason they are: this reads what the host already chose, before an update overwrites it.
# Keeping it out of install.sh also keeps that file under the ceiling its ratchet holds.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
#: The file the SERVICE reads, overridable so a test can seal it.
#:
#: NOT a convenience. `.env` is an ambient input to every caller of this script, and until 2026-09-01
#: nothing could hold it still: the moment an operator set `API_KEY` for real, four tests in
#: `test_the_environment_bridge_gets_the_key_the_service_uses.py` began failing -- correctly, because
#: the CONFLICT guard below fired on the shell key they set versus the file key they could not see.
#: They sealed both shell names and believed that was the whole environment; it stopped being so when
#: this script learned to read the file.
#:
#: A test that cannot seal an input is a test whose result the host decides.
ENV_FILE="${AIFY_ENV_FILE:-$REPO_ROOT/.env}"

#: The floor a key must clear, named ONCE. The generator held itself to this and nothing checked a
#: key that arrived any other way, so an operator's weak key was reused as though it had been vetted.
MIN_KEY_LENGTH=32

#: Matches the shapes the grammar table above records, and nothing else.
KEY_LINE_PATTERN='^[[:space:]]*(export[[:space:]]+)?API_KEY[[:space:]]*='

#: Turn ONE matched line into the value Compose would give it, or fail loudly.
#: Never `eval`, never sourced: `.env` is operator-edited, and a stray backtick or `$(...)` in any
#: line of it would EXECUTE while this runs as the operator.
value_from_line() {
  local line="$1" value
  value="${line#*=}"
  # Leading whitespace only. Trailing is handled per-form below, because a quoted value keeps its
  # inner spaces and an unquoted one does not.
  value="$(printf '%s' "$value" | sed 's/^[[:space:]]*//' | tr -d '\r')"

  case "$value" in
    \"*)
      value="$(printf '%s' "$value" | sed 's/[[:space:]]*$//')"
      case "$value" in
        *\") value="${value#\"}"; value="${value%\"}" ;;
        *) echo "ERROR: API_KEY in $ENV_FILE opens with a quote it never closes." >&2; return 5 ;;
      esac
      case "$value" in
        *\\*) echo "ERROR: API_KEY in $ENV_FILE contains a backslash inside quotes. Compose applies" >&2
              echo "       escape rules there that this does not reimplement, so the value it reads" >&2
              echo "       and the value clients would be given could differ. Use an unquoted key." >&2
              return 5 ;;
      esac
      ;;
    \'*)
      value="$(printf '%s' "$value" | sed 's/[[:space:]]*$//')"
      case "$value" in
        *\') value="${value#\'}"; value="${value%\'}" ;;
        *) echo "ERROR: API_KEY in $ENV_FILE opens with a quote it never closes." >&2; return 5 ;;
      esac
      case "$value" in
        *\\*) echo "ERROR: API_KEY in $ENV_FILE contains a backslash inside quotes. Compose applies" >&2
              echo "       escape rules there that this does not reimplement, so the value it reads" >&2
              echo "       and the value clients would be given could differ. Use an unquoted key." >&2
              return 5 ;;
      esac
      ;;
    *)
      # An inline comment, but ONLY when a `#` follows whitespace -- measured: `val#nospace` keeps
      # its hash, `val #c` does not. Then trailing whitespace.
      value="$(printf '%s' "$value" | sed 's/[[:space:]][[:space:]]*#.*$//; s/[[:space:]]*$//')"
      ;;
  esac
  printf '%s' "$value"
}

#: The value the SERVICE will see: the LAST definition, the way Compose resolves an env_file.
#: A READ FAILURE IS NOT AN ABSENCE. `grep` exits 1 for "no match" and 2 or more for a real error,
#: and collapsing those with `|| true` is the same defect this script exists to fix, one level down.
key_from_env_file() {
  [ -e "$ENV_FILE" ] || return 0
  if [ ! -r "$ENV_FILE" ]; then
    echo "ERROR: $ENV_FILE exists but cannot be read, so this cannot tell whether a key is set." >&2
    return 1
  fi

  local matches status
  set +e
  matches="$(grep -E "$KEY_LINE_PATTERN" "$ENV_FILE")"
  status=$?
  set -e
  if [ "$status" -gt 1 ]; then
    echo "ERROR: reading $ENV_FILE failed (grep exit $status). That is not the same as having no" >&2
    echo "       key, and treating it as one would write a keyless config after a failure." >&2
    return 1
  fi
  [ -n "$matches" ] || return 0

  value_from_line "$(printf '%s' "$matches" | tail -n1)"
}

key_from_shell() {
  # Taken verbatim. These are already environment VALUES, not `.env` syntax, so the quote and
  # comment rules above would corrupt a key that legitimately contains a `#`.
  if [ -n "${CLAUDE_MCP_API_KEY:-}" ]; then printf '%s' "$CLAUDE_MCP_API_KEY"; return 0; fi
  if [ -n "${AIFY_API_KEY:-}" ]; then printf '%s' "$AIFY_API_KEY"; return 0; fi
  return 0
}

key_is_weak() {
  [ ${#1} -lt $MIN_KEY_LENGTH ]
}

#: Resolve ONCE, and let a disagreement be an answer rather than a silent preference. A shell key and
#: a different `.env` key is the case that configures every client with a value the service will
#: refuse: the clients get the shell key, the service restarts onto the file key, and all of it looks
#: correctly installed. There is a right action available, so this refuses and names it.
resolve_key() {
  local shell_key file_key
  shell_key="$(key_from_shell)"
  file_key="$(key_from_env_file)" || return $?

  if [ -n "$shell_key" ] && [ -n "$file_key" ] && [ "$shell_key" != "$file_key" ]; then
    echo "ERROR: this shell and $ENV_FILE name DIFFERENT API keys." >&2
    echo "       Clients would be configured with the shell's key and the service would run on the" >&2
    echo "       file's, so every call would 401 with both halves looking correct." >&2
    echo "       Unset CLAUDE_MCP_API_KEY / AIFY_API_KEY to use the service's own key, or set" >&2
    echo "       API_KEY in $ENV_FILE to match the shell." >&2
    return 3
  fi

  local key="" source=""
  if [ -n "$shell_key" ]; then key="$shell_key"; source="this shell"
  elif [ -n "$file_key" ]; then key="$file_key"; source="$ENV_FILE"
  fi
  [ -n "$key" ] || return 0

  # REPORTED ON THE READ PATH, REFUSED ON `--generate`. A weak key that is already in use is the
  # operator's running state: the service is on it and every installed bridge holds it, so aborting
  # an ordinary install neither rotates it nor helps. `--generate` is different -- it is the path
  # that exists to ESTABLISH a key, and adopting a guessable one there is the thing it must not do.
  if key_is_weak "$key"; then
    echo "WARNING: the API key from $source is ${#key} characters; $MIN_KEY_LENGTH is the floor this" >&2
    echo "         project generates to. It reads as protection while being guessable." >&2
  fi
  printf '%s\n' "$key"
}

#: Write `.env` with exactly ONE API_KEY line, atomically, and NEVER on the strength of a read that
#: failed. The first version of this ran `grep -v ... > "$tmp" || true` and then moved `$tmp` into
#: place: an unreadable or erroring `.env` produced an EMPTY temp file, and the move replaced the
#: operator's entire configuration with a single API_KEY line. `|| true` on a read, followed by a
#: write derived from it, is how a swallowed error becomes data loss.
persist_key() {
  local key="$1" tmp kept status
  if [ -e "$ENV_FILE" ]; then
    if [ ! -r "$ENV_FILE" ]; then
      echo "ERROR: refusing to rewrite $ENV_FILE because it cannot be read. Replacing it would" >&2
      echo "       discard every other setting in it." >&2
      return 1
    fi
    set +e
    kept="$(grep -Ev "$KEY_LINE_PATTERN" "$ENV_FILE")"
    status=$?
    set -e
    if [ "$status" -gt 1 ]; then
      echo "ERROR: refusing to rewrite $ENV_FILE: reading it failed (grep exit $status). Writing" >&2
      echo "       what that read returned would discard every other setting in it." >&2
      return 1
    fi
  else
    kept=""
  fi

  tmp="$(mktemp "$ENV_FILE.XXXXXX")"
  # `trap` rather than a tidy-up line: an interrupt between here and the move would otherwise leave
  # the temp file beside the operator's `.env`, where the next glob or backup sweep finds a key.
  trap 'rm -f "$tmp"' EXIT
  [ -z "$kept" ] || printf '%s\n' "$kept" > "$tmp"
  printf 'API_KEY=%s\n' "$key" >> "$tmp"
  mv -f "$tmp" "$ENV_FILE"
  trap - EXIT
}

generate_key() {
  local existing
  existing="$(resolve_key)" || return $?
  if [ -n "$existing" ]; then
    if key_is_weak "$existing"; then
      echo "ERROR: refusing to adopt an API key of ${#existing} characters; $MIN_KEY_LENGTH is the" >&2
      echo "       floor. --generate exists to ESTABLISH the key every client will be given, and a" >&2
      echo "       guessable one reads as protection while being none. Rotating means re-running" >&2
      echo "       install.sh for each client, so this refuses rather than deciding that for you." >&2
      return 4
    fi
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
  if key_is_weak "$key"; then
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
