#!/bin/bash
# Where the INSTALLED environment-bridge launcher sends managed spawns.
#
#   bash scripts/installed-delegation.sh [dir]   # prints the aify-env endpoint, or nothing
#
# The third setting an update can silently discard, after the endpoint and the notification hook.
# `redeploy.sh` re-renders the launcher by calling install.sh, and install.sh bakes delegation only
# when asked -- so an update whose entire promise is "nothing changes but the code" moved managed
# spawns back off aify-env. Observed 2026-08-25, minutes after the flip: `spawn-delegation` went from
# `delegated` to `local` across a routine redeploy.
#
# It READS the launcher. Asking it by running it starts an environment bridge, which supersedes the
# live one and reaps its managed workers.
#
# Prints nothing and exits 1 when delegation is off or unreadable. Absence stays absence: a caller that
# received a default here could not tell a recovered setting from an invented one, and inventing this
# particular one would point spawns at a daemon nobody chose.

set -uo pipefail

dir="${1:-$HOME/.local/bin}"
file="$dir/aify-comms"
[ -r "$file" ] || exit 1

on="$(grep -oE '^export AIFY_COMMS_DELEGATE_SPAWNS="[^"]*"' "$file" 2>/dev/null \
  | head -1 | sed -E 's/.*="([^"]*)"$/\1/')"
[ -n "${on// /}" ] || exit 1

endpoint="$(grep -oE '^export AIFY_ENV_ENDPOINT="[^"]*"' "$file" 2>/dev/null \
  | head -1 | sed -E 's/.*="([^"]*)"$/\1/')"
# Delegation on with no endpoint is a launcher we should not reproduce: install.sh would default it,
# and defaulting silently is what this reader exists to prevent.
[ -n "${endpoint// /}" ] || exit 1

printf '%s\n' "$endpoint"
