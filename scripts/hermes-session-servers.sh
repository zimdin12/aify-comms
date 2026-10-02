#!/bin/bash
# The servers opted into every session ("sessionInject": {"mcp": true} in the service registry), given to
# hermes through the pinned aify-wrapper's hermes-config-cli.mjs. Hermes has no per-session way in, so it writes
# them into the user's hermes config with hermes' own `config set`, marked as aify-wrapper's, and they reach every
# hermes session on the host (aify-wrapper docs/REGISTRY.md).
#
#   bash scripts/hermes-session-servers.sh --check [registry-path]   refuse what could not be written; starts no hermes
#   bash scripts/hermes-session-servers.sh [registry-path]           write them
#
# The check runs before the launcher is rendered, the write after it, as aify-wrapper's own installer does. Exit 78
# with the failing step named on stderr when the tool refuses. FAILS CLOSED, like registry-fragment.sh: a missing
# tool is an error, never "nothing opted in".

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
check=""
if [ "${1:-}" = "--check" ]; then check="--check"; shift; fi
registry="${1:-$HOME/.aify/services.json}"
cli="$HERE/../mcp/stdio/node_modules/aify-wrapper/lib/hermes-config-cli.mjs"

# Git Bash's /c/... is not a path Windows node can open; see registry-fingerprint.sh.
for_node() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf %s "$1"; fi
}

if ! command -v node >/dev/null 2>&1 || [ ! -f "$cli" ]; then
  echo "hermes-session-servers: node or the pinned hermes config tool ($cli) is missing; run 'npm install' in mcp/stdio" >&2
  exit 1
fi
node "$(for_node "$cli")" $check "$(for_node "$registry")"
