#!/bin/bash
# WHICH OF THE THREE COMPONENTS THIS HOST HAS, and at what version.
#
#   bash scripts/components.sh            # one line per component, always three lines
#   bash scripts/components.sh --missing  # only the ones that are absent; exit 1 if any are
#
# Output is `<name> <state> <version> <how to get it>`, tab-separated, so a caller can render it or
# test it without re-deriving anything. `state` is `installed` or `missing`.
#
# WHY THIS EXISTS. Installing aify-comms installs one third of the system. The operator asked for the
# installer to account for all three -- "installing is now including 3 components and each repo has
# its own install instructions" -- and until now the aify-comms installer finished by describing
# only itself, on a host where a missing aify-env means managed spawns cannot run at all. A component
# nobody is told about is one nobody installs.
#
# IT NEVER RUNS WHAT IT MEASURES, and that is the whole design constraint rather than a nicety. A
# bare `aify-env` STARTS the host tier: it supersedes whichever instance is serving this machine, and
# the predecessor reaps its managed workers on the way out. `aify-comms` had the same property until
# v0.6.1 and took the fleet down twice. So presence is `command -v`, which resolves a name without
# executing it, and the version is READ from the installed package's own `package.json`. Asking a
# component what version it is, by running it, is how a version check becomes an outage.
#
# ABSENCE IS ABSENCE. A component that cannot be found prints `missing` with an empty version rather
# than a guess: an operator who is handed an invented version cannot tell it from a real one, and the
# whole point here is to say which of three things is not on this machine.

set -uo pipefail

only_missing=false
render=false
case "${1:-}" in
  --missing) only_missing=true ;;
  # THE HUMAN FORM LIVES HERE, not in install.sh's tail. The installer needs ONE line to call this,
  # and every line it does not carry is a line off a file that is already three times the size limit
  # and held by a ratchet. Rendering beside the reader also means the two cannot disagree about what
  # `missing` looks like.
  --render) render=true ;;
esac

#: WHERE npm PUT THE GLOBAL PACKAGES, asked of npm rather than assumed. The path differs per install
#: (nvm, volta, a system node, Windows vs POSIX), and a hardcoded guess reports every component
#: missing on a host that has all three -- which reads exactly like a broken install.
npm_root=""
if command -v npm >/dev/null 2>&1; then
  npm_root="$(npm root -g 2>/dev/null | tr -d '\r' || true)"
  # MSYS/Git Bash hands back a `C:\...` path that `[ -r ]` cannot open. cygpath is the translator
  # when it exists; elsewhere the value is already POSIX and passes through unchanged.
  if [ -n "$npm_root" ] && command -v cygpath >/dev/null 2>&1; then
    npm_root="$(cygpath -u "$npm_root" 2>/dev/null || printf '%s' "$npm_root")"
  fi
fi

# `<component>|<command that proves it is installed>|<npm package name, or empty>|<how to get it>`
COMPONENTS="\
aify-comms|aify-comms|,|clone https://github.com/zimdin12/aify-comms and run ./install.sh
aify-env|aify-env|aify-env|clone https://github.com/zimdin12/aify-env and run ./install.sh (it ASKS for the service key this host needs)
aify-wrapper|aify-wrapper-check|aify-wrapper|installed as a dependency of the two above; aify-wrapper-install --all --endpoint <url>"

package_version() {
  # READ, never executed. An absent or unparsable file yields "" -- see ABSENCE IS ABSENCE above.
  local pkg="$1" file
  [ -n "$pkg" ] && [ "$pkg" != "," ] || return 0
  [ -n "$npm_root" ] || return 0
  file="$npm_root/$pkg/package.json"
  [ -r "$file" ] || return 0
  # One field, first match, no JSON parser: this runs inside an installer that must work before any
  # dependency is present. `head -1` because a package.json can name a dependency's version too.
  grep -m1 '"version"[[:space:]]*:' "$file" 2>/dev/null \
    | sed -E 's/.*"version"[[:space:]]*:[[:space:]]*"([^"]*)".*/\1/' \
    | head -1
}

missing=0
[ "$render" = true ] && echo "Components on this host:"
while IFS='|' read -r name probe pkg howto; do
  [ -n "$name" ] || continue
  if command -v "$probe" >/dev/null 2>&1; then
    state="installed"
  else
    state="missing"
    missing=$((missing + 1))
  fi
  version=""
  [ "$state" = "installed" ] && version="$(package_version "$pkg")"
  if [ "$only_missing" = true ] && [ "$state" = "installed" ]; then
    continue
  fi
  if [ "$render" = true ]; then
    if [ "$state" = "installed" ]; then
      printf '  %s: installed%s\n' "$name" "${version:+ $version}"
    else
      printf '  %s: MISSING -- %s\n' "$name" "$howto"
    fi
  else
    printf '%s\t%s\t%s\t%s\n' "$name" "$state" "$version" "$howto"
  fi
done <<EOF
$COMPONENTS
EOF

[ "$missing" -eq 0 ] || exit 1
exit 0
