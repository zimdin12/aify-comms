#!/usr/bin/env bash
set -euo pipefail
script_dir="$(cd -- "$(dirname -- "$0")" && pwd)"
entry="$script_dir/staging/main.mjs"
# Node needs a native path even when the caller deliberately disabled MSYS path conversion.
if command -v cygpath >/dev/null 2>&1; then entry="$(cygpath -w "$entry")"; fi
exec node "$entry" "$@"
