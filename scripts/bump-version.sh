#!/usr/bin/env bash
# Bump the release version in every place that declares one, as ONE operation.
#
# WHY THIS EXISTS. The version lives in six files that must agree, and two tests fail the suite when
# they do not. Keeping them in step by hand has already failed silently three times: `plugin.json`
# was missing from the written recipe for three releases even though the test had always asserted
# it, and `install.sh` was missing for the same reason. The v0.5.x series turns roughly ten more
# releases' worth of that ritual into ten more chances to miss one -- so the ritual becomes a
# command, and the consistency tests become its gate rather than its afterthought.
#
# It deliberately does NOT tag, commit, build, or install. Those are decisions; this is bookkeeping.
#
#   bash scripts/bump-version.sh 0.5.1
#   bash scripts/bump-version.sh --check          # verify the six agree, change nothing
set -euo pipefail

cd "$(dirname "$0")/.."

VERSION_FILE="VERSION"
JS="mcp/stdio/version.js"
PKG="mcp/stdio/package.json"
LOCK="mcp/stdio/package-lock.json"
PLUGIN=".claude-plugin/plugin.json"

current() { tr -d ' \t\r\n' < "$VERSION_FILE"; }

# Read what each file currently declares, so --check reports the truth rather than assuming.
declared() {
  local cur; cur="$(current)"
  printf '%-34s %s\n' "$VERSION_FILE" "$cur"
  printf '%-34s %s\n' "$JS"     "$(sed -n 's/.*AIFY_VERSION = "\([^"]*\)".*/\1/p' "$JS" | head -1)"
  printf '%-34s %s\n' "$PKG"    "$(sed -n '0,/"version"/s/.*"version": "\([^"]*\)".*/\1/p' "$PKG" | head -1)"
  printf '%-34s %s\n' "$LOCK"   "$(sed -n '0,/"version"/s/.*"version": "\([^"]*\)".*/\1/p' "$LOCK" | head -1)"
  # The SECOND root declaration, under packages."". --check missed this originally, which is how a
  # half-bumped lock file passed a "green" verification.
  printf '%-34s %s\n' "$LOCK (packages.\"\")" \
    "$(awk '/"": \{/{f=1} f && /"version":/{gsub(/.*"version": "|".*/,""); print; exit}' "$LOCK")"
  printf '%-34s %s\n' "$PLUGIN" "$(sed -n '0,/"version"/s/.*"version": "\([^"]*\)".*/\1/p' "$PLUGIN" | head -1)"
}

if [[ "${1:-}" == "--check" ]]; then
  echo "Declared versions:"
  declared
  # $NF, not $2: one label contains a space (`(packages."")`) and $2 read the LABEL, so --check
  # reported a mismatch for six identical versions.
  distinct="$(declared | awk '{print $NF}' | sort -u | wc -l)"
  if [[ "$distinct" -ne 1 ]]; then
    echo
    echo "MISMATCH: the declarations disagree. Run: bash scripts/bump-version.sh <version>" >&2
    exit 1
  fi
  echo
  echo "All declarations agree."
  exit 0
fi

NEW="${1:-}"
if [[ ! "$NEW" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "usage: bash scripts/bump-version.sh <major.minor.patch> | --check" >&2
  exit 2
fi

OLD="$(current)"
echo "$OLD -> $NEW"

printf '%s\n' "$NEW" > "$VERSION_FILE"
# version.js: the single exported constant, not any version-shaped string that happens to be nearby.
sed -i "s/^export const AIFY_VERSION = \".*\";/export const AIFY_VERSION = \"$NEW\";/" "$JS"

# The JSON manifests are edited line-precisely rather than with sed, and NOT by reserialising the
# JSON (that would reformat files nobody asked to reformat).
#
# package-lock.json declares the root package's version TWICE: once at the top level and again under
# packages."". A first-match-only sed silently bumps one and leaves the other, which is exactly the
# half-done release this script exists to prevent -- my own first draft did it, and `--check` did
# not catch it because --check only reads the top-level key. A global replace is worse: the lock
# file carries a "version" for every dependency and would rewrite all of them.
NEW="$NEW" PKG="$PKG" LOCK="$LOCK" PLUGIN="$PLUGIN" python - <<'PY'
import io, os, re

new = os.environ["NEW"]
version_line = re.compile(r'^(\s*)"version":\s*".*?"(,?)\s*$')


def bump_first(path, count=1, after=None):
    """Rewrite the first `count` root-level "version" lines, optionally only after a marker line."""
    lines = io.open(path, encoding="utf-8").read().split("\n")
    armed = after is None
    done = 0
    for i, line in enumerate(lines):
        if not armed:
            if after in line:
                armed = True
            continue
        m = version_line.match(line)
        if m:
            lines[i] = f'{m.group(1)}"version": "{new}"{m.group(2)}'
            done += 1
            if done >= count:
                break
    if done < count:
        raise SystemExit(f"FAILED: {path} — expected {count} version line(s) after {after!r}, hit {done}")
    io.open(path, "w", encoding="utf-8", newline="\n").write("\n".join(lines))


bump_first(os.environ["PKG"])
bump_first(os.environ["PLUGIN"])
bump_first(os.environ["LOCK"])                       # top-level
bump_first(os.environ["LOCK"], after='"": {')        # packages."" — the one a sed misses
PY

echo
declared
echo
echo "Now: run the suites, then 'bash scripts/stamp.sh', then rebuild."
echo "If anything under mcp/stdio/ changed, re-run install.sh per client BEFORE tagging --"
echo "'aify-comms doctor' fails bridge-installed until you do, and the release is not shippable red."
