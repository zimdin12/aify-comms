#!/usr/bin/env bash
# Hand a plain `install.sh --client <runtime> [url]` to aify-env's guided-install chain (plan guided-install S2).
#
#   chain-handoff.sh <client> <server-url> <this install.sh>
#
# THE CHAIN IS ENTERED ONCE. install.sh calls this only with neither `--acquire-only` nor `--assets-only` and with no
# AIFY_INSTALL_CHAIN mark, and the chain calls install.sh back only in those two modes. So the chain runs once.
#
# WHERE THE CHAIN COMES FROM. `aify-env install`, run as its module in npm's global aify-env, never through the
# `aify-env` command: an aify-env too old to know `install` must not be handed an argument it could misread as a
# start, which supersedes the running environment. When aify-env is not installed at all, it is installed in its git
# form first (phase 1 only). An aify-env that is installed but has no chain is NOT replaced:
# that is most often a developer's `npm link` of a checkout, and swapping it for a package would change what the
# host runs. The full install continues instead, and the line below says so.
#
# APPROVAL is the operator's: an artifact tuple the catalogue does not hold needs `--approve <digest>`, which an
# unattended run passes as AIFY_INSTALL_APPROVE (digests separated by spaces), exactly as the refusal printed them.
#
# THE KEY never travels in argv or in the chain's environment: install.sh hands it over as AIFY_API_KEY, and it
# reaches the chain only through a private file, removed when the chain returns. The chain and every installer it runs
# start without this service's key variables, so a later registration can only name the published reference.
#
# Exit: the chain's status; 3 when nothing was handed off and install.sh should continue its own full path.
set -uo pipefail

CLIENT="${1:?client}"
URL="${2:?server url}"
INSTALLER="${3:?installer path}"
CONTINUE=3

chain_module() { local root; root="$(npm root -g 2>/dev/null)" && [ -f "$root/aify-env/bin/aify-env-install.mjs" ] && printf '%s' "$root/aify-env/bin/aify-env-install.mjs"; }
CHAIN="$(chain_module)"
if [ -z "$CHAIN" ]; then
  if command -v aify-env >/dev/null 2>&1; then
    echo "aify-env is installed without the guided-install chain (aify-env install); continuing this installer's full path." >&2
    echo "  Update aify-env to use the chain, or pass --standalone to say this path is meant." >&2
    exit "$CONTINUE"
  fi
  echo "Installing aify-env (${AIFY_INSTALL_ENV_SOURCE:-github:zimdin12/aify-env}) to run the guided-install chain..." >&2
  npm install -g --no-audit --no-fund "${AIFY_INSTALL_ENV_SOURCE:-github:zimdin12/aify-env}" || exit 1
  CHAIN="$(chain_module)" || { echo "aify-env was installed, but it holds no guided-install chain." >&2; exit 1; }
fi

args=(--connect "aify-comms=$URL" --client "$CLIENT" --installer "aify-comms=$INSTALLER")
[ -t 0 ] || args+=(--no-prompt)
for digest in ${AIFY_INSTALL_APPROVE:-}; do args+=(--approve "$digest"); done
keydir=""
if [ -n "${AIFY_API_KEY:-}" ]; then
  keydir="$(mktemp -d)"
  ( umask 077; printf '%s' "$AIFY_API_KEY" > "$keydir/credentialRef" )
  args+=(--secret "aify-comms.credentialRef=$keydir/credentialRef")
fi
status=0
env -u AIFY_API_KEY -u CLAUDE_MCP_API_KEY node "$CHAIN" "${args[@]}" || status=$?
[ -z "$keydir" ] || rm -rf -- "${keydir:?}"
exit "$status"
