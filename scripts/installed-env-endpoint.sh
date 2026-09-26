#!/bin/bash
# Which aify-env endpoint is already installed in a launcher directory.
#
#   bash scripts/installed-env-endpoint.sh [dir]     # prints the URL, or nothing
#
# `install.sh --env-endpoint <url>` bakes the aify-env the doctor asks into the `aify-comms` launcher.
# redeploy.sh re-runs the installer, and without this it passed no --env-endpoint, so every routine
# update reset a custom endpoint to the default 127.0.0.1:8802 (v0.7 review). The sibling of
# scripts/installed-endpoint.sh, which recovers the service URL the same way.
#
# It READS the file: running the launcher to ask would be running the verifier for no reason.
# Prints nothing and exits 1 when there is no endpoint to recover, so absence stays absent.

set -uo pipefail

dir="${1:-$HOME/.local/bin}"
file="$dir/aify-comms"
[ -f "$file" ] || exit 1

found="$(grep -oE '^export AIFY_ENV_ENDPOINT="https?://[^"]+"$' "$file" 2>/dev/null \
  | head -1 | sed -E 's/^export AIFY_ENV_ENDPOINT="([^"]+)"$/\1/')"

case "$found" in
  ""|*@@*) exit 1 ;;
esac

printf '%s' "$found"
