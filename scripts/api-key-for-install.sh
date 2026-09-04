#!/usr/bin/env bash
# Resolve the API key for an install, and ACT on what the resolver said.
#
# WHY THIS IS ITS OWN SCRIPT, and it is the same reason `api-key.sh`, `hook-installed.sh` and
# `installed-endpoint.sh` are: `install.sh` is 3,000 lines and already past the size gate, so logic
# that can live beside it should. The decision here is also the kind that wants testing without
# running an installer that rewrites this machine's MCP configuration.
#
# WHAT IT FIXES. External review, Round 8 H8. `install.sh` called `api-key.sh --ask || true` and then
# silently skipped the export, so:
#
#   - An unattended install finished with NO key, no credential carrier, and nothing said. The
#     operator found out when the service started refusing every client that had just been installed.
#     The README's own intended install path is a coding agent pointed at the repo, which has no tty,
#     so this was the DEFAULT outcome and not an edge case.
#   - `|| true` swallowed exit 3 (the shell and `.env` name DIFFERENT keys) and exit 5 (a `.env`
#     value that cannot be parsed the way Compose parses it). Both were read as "no key, carry on",
#     which is how the wrong credential gets baked into every client on the host.
#
# THE ASK ITSELF IS RIGHT AND IS NOT CHANGED. `api-key.sh --ask` answering 0 with an empty key when
# there is no `/dev/tty` is correct -- an installer that hangs waiting for an answer nobody can give
# is worse, and a prompt written to a missing device teaches an operator that errors are normal. Its
# own test says "the caller decides what to do". This is the caller, deciding.
#
# CONTRACT: prints the key (possibly empty) on stdout, explains itself on stderr, and exits
#   0  a key, or an honest absence -- the caller may proceed
#   1  the install must stop: the two sources disagree, one cannot be read the way Compose reads it,
#      or `--generate` was asked for and no key could be made
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

#: `--generate` when the operator asked for a key to be MADE (`install.sh --with-api-key`); anything
#: else is the ordinary resolve-or-ask path. Both live here so one script answers "what key does this
#: install use", rather than the question being half in the installer and half here.
MODE="${1:-}"

if [ "$MODE" = "--generate" ]; then
  KEY="$(bash "$HERE/api-key.sh" --generate)" || {
    echo "ERROR: --with-api-key was requested but no key could be generated." >&2
    exit 1
  }
  # STDERR. Everything this script prints on STDOUT is the key -- the caller does
  # `RESOLVED_API_KEY="$(...)"` -- so a status line written to stdout would BE the credential baked
  # into every client. Caught here rather than by an install that 401'd everywhere.
  echo "API key in place (.env). Until 'docker compose up -d' the service still accepts unauthenticated requests." >&2
  printf '%s' "$KEY"
  exit 0
fi

set +e
KEY="$(bash "$HERE/api-key.sh" --ask)"
STATUS=$?
set -e

case "$STATUS" in
  0) : ;;
  3)
    echo "ERROR: the API key in your shell and the one in .env are DIFFERENT." >&2
    echo "  Installing now would bake one of them into every client and you would not know which." >&2
    echo "  Resolve them (unset API_KEY, or correct .env), then re-run this installer." >&2
    exit 1
    ;;
  5)
    echo "ERROR: API_KEY in .env cannot be read the way Docker Compose reads it." >&2
    echo "  The service and this installer would resolve DIFFERENT values. Fix the quoting in .env," >&2
    echo "  then re-run this installer." >&2
    exit 1
    ;;
  *)
    # An installer that carries on past an unexpected failure in its own credential resolver is the
    # shape this repo has been bitten by before.
    echo "ERROR: the API key resolver failed unexpectedly (exit $STATUS)." >&2
    echo "  Refusing to install clients whose credential could not be determined." >&2
    exit 1
    ;;
esac

# SAID, NOT ASSUMED. The whole of H8 is that this outcome was silent. It stays a legal configuration
# -- a loopback-only host with no key is a choice this project supports -- so this REPORTS rather
# than refuses. What it must not do is let an operator believe a key was installed.
if [ -z "$KEY" ]; then
  echo "No API key: this host has none set and there was no terminal to ask at." >&2
  echo "  The clients being installed will connect WITHOUT a credential. If the service is running" >&2
  echo "  with API_KEY set, they will be refused." >&2
  echo "  To set one: re-run with --with-api-key, or put API_KEY in .env and re-run." >&2
fi

printf '%s' "$KEY"
