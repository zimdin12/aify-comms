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
chain_module() { [ -f "$root/aify-env/bin/aify-env-install.mjs" ] && printf '%s' "$root/aify-env/bin/aify-env-install.mjs"; }
CHAIN="$(chain_module)"
if [ -z "$CHAIN" ] && { [ -L "$root/aify-env" ] || [ -e "$root/aify-env/.git" ]; }; then
  echo "aify-env here is a linked checkout ($root/aify-env) without the guided-install chain, and it is never modified." >&2
  echo "  Update your checkout (git pull), or rerun with --standalone to use this installer's full path." >&2
  exit 78
fi

# ONE OWNER. An aify-env on PATH must be npm's own bin for the global package this would use or update, or which
# installation runs and which one is updated are different things. Its bin is recognised in npm's two layouts, never
# by running it (an old one could read an argument as a start): a link resolving to the package's
# bin/aify-env.mjs (Unix), or, in the prefix beside the root, exactly the shim npm's own cmd-shim writes for that file
# (Windows). A shim is judged by what it is, byte for byte, never by a string it contains: a launcher can name the
# package's bin in a comment and exec something else. Anything else, including an unknown prefix or no cmd-shim to
# regenerate it with, is refused before a module is chosen or anything installed.
unix_path() { if command -v cygpath >/dev/null 2>&1; then cygpath -u "$1"; else printf '%s' "$1"; fi; }
native_path() { if command -v cygpath >/dev/null 2>&1; then cygpath -w "$1"; else printf '%s' "$1"; fi; }
# npm's cmd-shim run for the package's bin in a private copy of the prefix layout (the shim's text depends only on the
# path from the prefix to the bin and on the bin's first line), and compared with the file on PATH.
npm_shim_matches() {
  local on_path="$1" shim="$root/npm/node_modules/cmd-shim" tmp rc=1
  [ -f "$shim/package.json" ] || return 1
  tmp="$(mktemp -d)" || return 1
  mkdir -p "$tmp/node_modules/aify-env/bin" \
    && head -n 1 "$root/aify-env/bin/aify-env.mjs" > "$tmp/node_modules/aify-env/bin/aify-env.mjs" \
    && node -e 'require(process.argv[1])(process.argv[2], process.argv[3]).catch(() => process.exit(1))' \
      "$(native_path "$shim")" "$(native_path "$tmp/node_modules/aify-env/bin/aify-env.mjs")" "$(native_path "$tmp/aify-env")" \
    && cmp -s "$tmp/aify-env" "$on_path" && rc=0
  rm -rf "${tmp:?}"
  return $rc
}
npm_owns() {
  local on_path="$1" target pkg_bin prefix
  target="$(realpath "$on_path" 2>/dev/null)" && pkg_bin="$(realpath "$root/aify-env/bin/aify-env.mjs" 2>/dev/null)" \
    && [ -n "$target" ] && [ "$target" = "$pkg_bin" ] && return 0
  prefix="$(npm prefix -g 2>/dev/null)" && [ -n "$prefix" ] || return 1
  [ "$(unix_path "$(dirname "$on_path")")" = "$(unix_path "$prefix")" ] || return 1
  [ "$(unix_path "$root")" = "$(unix_path "$prefix")/node_modules" ] || return 1
  npm_shim_matches "$on_path"
}
if on_path="$(command -v aify-env 2>/dev/null)" && [ -n "$on_path" ]; then
  if [ ! -e "$root/aify-env" ]; then
    echo "aify-env is on PATH ($on_path) but npm did not install it, so it is not replaced." >&2
    echo "  Update it where it came from, or rerun with --standalone to use this installer's full path." >&2
    exit 78
  fi
  if ! npm_owns "$on_path"; then
    echo "The aify-env on PATH ($on_path) is not npm's own bin for $root/aify-env, so which installation runs is unknown; nothing was installed." >&2
    echo "  Remove the one that should not be there, or rerun with --standalone to use this installer's full path." >&2
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
