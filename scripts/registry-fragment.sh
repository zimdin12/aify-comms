#!/bin/bash
# The registry's two MCP fragments for a launcher, as the pinned aify-wrapper package computes them.
#
#   bash scripts/registry-fragment.sh [registry-path]
#
# Prints "<strict>|<session>|<codex>": `strict-fragment-b64` (the servers a strict-mode claude session adds,
# from services that set strictMcp), `session-fragment-b64` (the `--mcp-config` document every default-mode
# claude session gets, from services with "sessionInject": {"mcp": true}) and `session-codex-b64` (codex's
# `-c` words for the same services). Each is base64 and may be empty; base64 holds no `|`, so the bars
# separate them and an empty field stays a field. An absent registry is an empty one.
#
# FAILS CLOSED, unlike registry-fingerprint.sh. A fingerprint it cannot compute is reported as
# "unknown" and the launcher says so; a fragment it cannot compute would be rendered as "nothing opted
# in", which looks exactly like a registry with nothing in it. So a missing tool, or a registry the verbs
# refuse (exit 78, reasons on stderr), exits non-zero and install.sh stops. install.sh substituted an
# empty strict fragment unconditionally until 0.8 (KNOWN_ISSUES: strict MCP gap).

set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
registry="${1:-$HOME/.aify/services.json}"
cli="$HERE/../mcp/stdio/node_modules/aify-wrapper/lib/registry-cli.mjs"

# Git Bash's /c/... is not a path Windows node can open; see registry-fingerprint.sh.
for_node() {
  if command -v cygpath >/dev/null 2>&1; then cygpath -m "$1"; else printf %s "$1"; fi
}

if ! command -v node >/dev/null 2>&1 || [ ! -f "$cli" ]; then
  echo "registry-fragment: node or the pinned registry tool ($cli) is missing; run 'npm install' in mcp/stdio" >&2
  exit 1
fi
strict="$(node "$(for_node "$cli")" strict-fragment-b64 "$(for_node "$registry")")" || exit $?
session="$(node "$(for_node "$cli")" session-fragment-b64 "$(for_node "$registry")")" || exit $?
codex="$(node "$(for_node "$cli")" session-codex-b64 "$(for_node "$registry")")" || exit $?
printf '%s|%s|%s' "$strict" "$session" "$codex"
