#!/bin/bash
# The fingerprint of a service registry, as the aify-wrapper package computes it.
#
#   bash scripts/registry-fingerprint.sh [registry-path]
#
# A launcher bakes this at render time, and `aify-wrapper-check` compares it against the registry as it
# stands now -- that is how a launcher built against a stale registry says so instead of silently
# launching against one service. The tool is the PACKAGE's, deliberately: computing it here in a second
# implementation is how the two ends start disagreeing about what a fingerprint is while each stays
# internally consistent.
#
# install.sh baked the literal string "unknown" until 2026-08-20, so every launcher it produced read
# `??` and none ever read `current`. Refusing to call them fine was the right direction and told nobody
# anything; the fingerprint tool has shipped in the pinned dependency the whole time.
#
# Prints the fingerprint, or "unknown" if the tool could not run. An ABSENT registry is not that case:
# it is a legitimate host state that fingerprints as the empty registry, which is a real answer.

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
registry="${1:-$HOME/.aify/services.json}"
cli="$HERE/../mcp/stdio/node_modules/aify-wrapper/lib/registry-cli.mjs"

# Under Git-Bash `pwd` yields /c/Users/... , which Windows node resolves against the C: drive with the
# whole POSIX path appended and cannot find. It works in an interactive shell only because MSYS rewrites
# path-shaped arguments on their way to a native binary -- a behaviour of the shell, not of this script.
for_node() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf %s "$1"; fi
}

if ! command -v node >/dev/null 2>&1 || [ ! -f "$cli" ]; then
  printf 'unknown'
  exit 0
fi

out="$(node "$(for_node "$cli")" fingerprint "$(for_node "$registry")" 2>/dev/null)" || out=""
printf '%s' "${out:-unknown}"
