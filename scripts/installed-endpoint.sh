#!/bin/bash
# What endpoint is already installed in a launcher directory.
#
#   bash scripts/installed-endpoint.sh [dir]     # prints the URL, or nothing
#
# Two update paths need this and each used to carry its own copy of one regex:
#
#   * redeploy.sh, the documented one-command update, re-renders every wrapper and takes the URL from
#     one that is already installed.
#   * install.sh's interactive prompt, which offers it as the pre-filled default.
#
# Both matched `AIFY_SERVER_URL:-http://`, the shape wrappers had before the v0.6 harness contract.
# Current launchers carry HARNESS_ENDPOINT instead, so the match silently stopped happening -- and
# redeploy.sh fell through to its loopback default and would have rewritten every wrapper on the host
# to point at 127.0.0.1. On a fleet reaching a LAN address that is the whole fleet, during an update
# whose entire promise is that it changes nothing but the code.
#
# One reader, so the two paths cannot drift apart again. It READS the file: asking a launcher by
# running it would start a coding-agent runtime.
#
# Prints nothing and exits 1 when there is no endpoint to recover. Absence has to stay absent -- a
# caller that got a default here could not tell a recovered endpoint from an invented one.

set -uo pipefail

dir="${1:-$HOME/.local/bin}"

# Fixed order, so the answer does not depend on how the directory happens to be listed.
for name in claude-aify codex-aify hermes-aify pi-aify omp-aify; do
  file="$dir/$name"
  [ -f "$file" ] || continue

  # The current shape: HARNESS_ENDPOINT="${HARNESS_ENDPOINT-${AIFY_COMMS_URL:-<url>}}"
  found="$(grep -oE '^HARNESS_ENDPOINT="\$\{HARNESS_ENDPOINT-\$\{[A-Z0-9_]+:-[^}"]+\}\}"$' "$file" 2>/dev/null \
    | head -1 | sed -E 's/.*:-([^}"]+)\}\}"$/\1/')"

  # The pre-contract shape, still on disk for anyone updating from an older install.
  if [ -z "$found" ]; then
    found="$(grep -oE 'AIFY_SERVER_URL:-http://[^"}]+' "$file" 2>/dev/null \
      | head -1 | sed 's/^AIFY_SERVER_URL:-//')"
  fi

  # An unrendered template says @@ENDPOINT@@ here. Handing that back would bake the literal
  # placeholder in where a URL belongs.
  case "$found" in
    ""|@@*@@) continue ;;
  esac

  printf '%s' "$found"
  exit 0
done

exit 1
