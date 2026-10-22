#!/usr/bin/env bash
# Hand a plain `install.sh --client <runtime> [url]` to aify-env's guided-install chain (plan guided-install S2).
#
#   chain-handoff.sh <client> <server-url> <this install.sh>
#
# THE CHAIN IS ENTERED ONCE. install.sh calls this only with neither `--acquire-only` nor `--assets-only` and with no
# AIFY_INSTALL_CHAIN mark, and the chain calls install.sh back only in those two modes. So the chain runs once.
#
# WHERE THE CHAIN COMES FROM. The `aify-env-install` command on PATH. When aify-env is not installed at all, it is
# installed in its git form first (phase 1 only). An aify-env that is installed but has no chain is NOT replaced:
# that is most often a developer's `npm link` of a checkout, and swapping it for a package would change what the
# host runs. The full install continues instead, and the line below says so.
#
# APPROVAL is the operator's: an artifact tuple the catalogue does not hold needs `--approve <digest>`, which an
# unattended run passes as AIFY_INSTALL_APPROVE (digests separated by spaces), exactly as the refusal printed them.
#
# THE KEY never travels in argv: install.sh resolved it into AIFY_API_KEY, and it reaches the chain through a
# private file that is removed when the chain returns.
#
# Exit: the chain's status; 3 when nothing was handed off and install.sh should continue its own full path.
set -uo pipefail

CLIENT="${1:?client}"
URL="${2:?server url}"
INSTALLER="${3:?installer path}"
CONTINUE=3

if ! command -v aify-env-install >/dev/null 2>&1; then
  if command -v aify-env >/dev/null 2>&1; then
    echo "aify-env is installed without the guided-install chain (aify-env-install); continuing this installer's full path." >&2
    echo "  Update aify-env to use the chain, or pass --standalone to say this path is meant." >&2
    exit "$CONTINUE"
  fi
  echo "Installing aify-env (${AIFY_INSTALL_ENV_SOURCE:-github:zimdin12/aify-env}) to run the guided-install chain..." >&2
  npm install -g --no-audit --no-fund "${AIFY_INSTALL_ENV_SOURCE:-github:zimdin12/aify-env}" || exit 1
  command -v aify-env-install >/dev/null 2>&1 || { echo "aify-env was installed, but aify-env-install is not on PATH." >&2; exit 1; }
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
aify-env-install "${args[@]}" || status=$?
[ -z "$keydir" ] || rm -rf -- "${keydir:?}"
exit "$status"
