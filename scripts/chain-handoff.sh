#!/usr/bin/env bash
# Hand a plain `install.sh --client <runtime> [url]` to aify-env's guided-install chain (plan guided-install S2).
#
#   chain-handoff.sh <client> <server-url> <this install.sh>
#
# THE CHAIN IS ENTERED ONCE. install.sh calls this only with neither `--acquire-only` nor `--assets-only` and with no
# AIFY_INSTALL_CHAIN mark, and the chain calls install.sh back only in those two modes. So the chain runs once.
#
# EVERY REFUSAL COMES BEFORE ANY WRITE. Options the chain does not carry (`--with-hook`, `--mcp-transport`,
# `--env-endpoint`; install.sh passes them as WITH_HOOK, MCP_TRANSPORT and ENV_ENDPOINT_GIVEN) are refused first, then
# aify-env's place is settled. Only after both is the key resolved, which can write .env (`--with-api-key` generates
# one, WITH_API_KEY; an answer to the ask is kept), and only then is anything installed.
#
# WHERE THE CHAIN COMES FROM. `aify-env install`, run as its module in npm's global aify-env, never through the
# `aify-env` command: an aify-env too old to know `install` must not be handed an argument it could misread as a
# start, which supersedes the running environment. npm's global root must be KNOWN: if npm cannot say where it is, a
# protected link there cannot be ruled out, so nothing is installed. A missing or older packaged aify-env is installed
# in its git form first (phase 1 only). A LINKED one (a symlink or a git checkout, a developer's `npm link`) is never modified, and an
# aify-env on PATH that npm did not install is not ours to replace: both refuse, saying what to do. Nothing falls back
# to a pre-chain path without the operator choosing `--standalone`.
#
# APPROVAL is the operator's: an artifact tuple the catalogue does not hold needs `--approve <digest>`, which an
# unattended run passes as AIFY_INSTALL_APPROVE (digests separated by spaces), exactly as the refusal printed them.
#
# THE KEY never touches a file, argv or the chain's environment: resolved here by the installer's own resolver
# (api-key-for-install.sh, beside this script), it reaches the chain on stdin (`--secret-stdin`). The chain and every installer it runs start without this service's
# key variables, so a later registration can only name the published reference.
#
# Exit: the chain's status; 64 for options the chain does not carry; 78 when npm's global root is unknown or aify-env
# here may not be replaced; 1 when the key cannot be resolved.
set -uo pipefail

CLIENT="${1:?client}"
URL="${2:?server url}"
INSTALLER="${3:?installer path}"
SOURCE="${AIFY_INSTALL_ENV_SOURCE:-github:zimdin12/aify-env}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

uncarried=""
[ "${WITH_HOOK:-false}" = true ] && uncarried="$uncarried --with-hook"
[ -n "${MCP_TRANSPORT:-}" ] && uncarried="$uncarried --mcp-transport"
[ "${ENV_ENDPOINT_GIVEN:-false}" = true ] && uncarried="$uncarried --env-endpoint"
if [ -n "$uncarried" ]; then
  echo "install.sh:$uncarried: not carried by the guided-install chain yet, so nothing was written." >&2
  echo "  Rerun with --standalone to apply them on this installer's full path." >&2
  exit 64
fi

root="$(npm root -g 2>/dev/null)" && [ -n "$root" ] || {
  echo "Could not ask npm for its global root, so a linked aify-env there cannot be ruled out; nothing was installed." >&2
  echo "  Check that npm runs here, or rerun with --standalone to use this installer's full path." >&2
  exit 78
}
# ONE OWNER. An aify-env on PATH must be npm's global one (its bin directory is the global prefix, or prefix/bin), or
# which installation this updates and which one the operator runs are different things: refused, before anything is
# chosen or installed. npm's prefix unknown beside an aify-env on PATH is the same ambiguity.
unix_path() { if command -v cygpath >/dev/null 2>&1; then cygpath -u "$1"; else printf '%s' "$1"; fi; }
if on_path="$(command -v aify-env 2>/dev/null)" && [ -n "$on_path" ]; then
  prefix="$(npm prefix -g 2>/dev/null)" && [ -n "$prefix" ] || prefix=""
  dir="$(unix_path "$(dirname "$on_path")")"
  if [ -z "$prefix" ] || { [ "$dir" != "$(unix_path "$prefix")" ] && [ "$dir" != "$(unix_path "$prefix")/bin" ]; }; then
    [ -n "$prefix" ] || prefix="its prefix unknown"
    echo "The aify-env on PATH ($on_path) is not npm's global one ($prefix), so which installation to use is ambiguous; nothing was installed." >&2
    echo "  Remove one of them, or rerun with --standalone to use this installer's full path." >&2
    exit 78
  fi
fi
chain_module() { [ -f "$root/aify-env/bin/aify-env-install.mjs" ] && printf '%s' "$root/aify-env/bin/aify-env-install.mjs"; }
CHAIN="$(chain_module)"
if [ -z "$CHAIN" ]; then
  if [ -L "$root/aify-env" ] || [ -e "$root/aify-env/.git" ]; then
    echo "aify-env here is a linked checkout ($root/aify-env) without the guided-install chain, and it is never modified." >&2
    echo "  Update your checkout (git pull), or rerun with --standalone to use this installer's full path." >&2
    exit 78
  fi
  if [ ! -e "$root/aify-env" ] && command -v aify-env >/dev/null 2>&1; then
    echo "aify-env is on PATH ($(command -v aify-env)) but npm did not install it, so it is not replaced." >&2
    echo "  Update it where it came from, or rerun with --standalone to use this installer's full path." >&2
    exit 78
  fi
fi

[ "${WITH_API_KEY:-false}" = true ] && KEY_MODE=--generate || KEY_MODE=
KEY="$(bash "$HERE/api-key-for-install.sh" $KEY_MODE)" || exit 1

if [ -z "$CHAIN" ]; then
  echo "Installing aify-env ($SOURCE) to run the guided-install chain..." >&2
  npm install -g --no-audit --no-fund "$SOURCE" || exit 1
  CHAIN="$(chain_module)" || { echo "aify-env was installed, but it holds no guided-install chain." >&2; exit 1; }
fi

args=(--connect "aify-comms=$URL" --client "$CLIENT" --installer "aify-comms=$INSTALLER")
[ -t 0 ] || args+=(--no-prompt)
for digest in ${AIFY_INSTALL_APPROVE:-}; do args+=(--approve "$digest"); done
if [ -n "$KEY" ]; then
  args+=(--secret-stdin aify-comms.credentialRef)
  printf '%s' "$KEY" | env -u AIFY_API_KEY -u CLAUDE_MCP_API_KEY node "$CHAIN" "${args[@]}"
else
  env -u AIFY_API_KEY -u CLAUDE_MCP_API_KEY node "$CHAIN" "${args[@]}"
fi
