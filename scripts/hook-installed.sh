#!/bin/bash
# Is a notification hook already registered for this client on this host?
#
#   bash scripts/hook-installed.sh <claude|codex|hermes> [config-root]
#
# exit 0 = a hook is registered, exit 1 = none. Nothing is printed; the exit status is the answer.
#
# `--with-hook` is opt-in, and redeploy.sh -- the documented one-command update -- does not pass it.
# So every update printed "Notification hook skipped" and left the hook's REGISTRATION at whatever an
# older install wrote. The hook's code was never the exposure: notify-check.js lives in the bridge
# directory, which is mirrored on every install. What could go stale is the line that POINTS at it, and
# a changed command shape would keep the old one forever with the installer reporting success.
#
# Opting in once means opting in. The flag decides whether to install a hook that is not there; it does
# not decide whether to maintain one that is.
#
# The config root is a parameter so this is testable against fixtures rather than only against the
# operator's live configuration. Default per client when it is omitted.

set -uo pipefail

client="${1:-}"
root="${2:-}"

# The bridge script every client's hook command points at. Finding this name in a client's config is
# what distinguishes OUR hook from somebody else's, which is the whole question -- a config full of
# unrelated hooks must answer "no".
MARKER="notify-check"

case "$client" in
  claude)
    file="${root:-$HOME/.claude}/settings.json"
    ;;
  codex)
    file="${root:-${CODEX_HOME:-$HOME/.codex}}/hooks.json"
    ;;
  hermes)
    # No default, deliberately. hermes' config lives wherever `hermes_config_root` resolves --
    # $HERMES_HOME, or what `hermes config path` reports -- so ~/.hermes is a guess, and a guess here
    # answers "no hook" for the one client whose path is not derivable. That keeps the silent skip for
    # a single runtime while the other two look fixed, which is the hardest kind to notice. Unresolved
    # is unanswerable, and unanswerable is not "no".
    if [ -z "$root" ]; then
      echo "hook-installed.sh: hermes needs its config root passed; it cannot be derived" >&2
      exit 2
    fi
    file="$root/config.yaml"
    ;;
  *)
    # An unknown client is not evidence of absence. Say so on stderr and fail, rather than reporting a
    # confident "no hook" for something never looked at.
    echo "hook-installed.sh: unknown client '${client}'" >&2
    exit 2
    ;;
esac

[ -f "$file" ] || exit 1
grep -q "$MARKER" "$file" 2>/dev/null || exit 1
exit 0
