#!/bin/bash
# stamp.sh — write service/_build_stamp.json from the host git checkout.
#
# Because the Dockerfile COPY's service/ into the image but .dockerignore
# excludes .git (the container has no git history of its own), the build SHA
# must be STAMPED into a file under service/ at build time. This script does
# that. Run it BEFORE `docker compose up -d --build` (redeploy.sh does so).
#
# Safe to run outside a git checkout: falls back to "unknown" (or the env
# GIT_SHA / GIT_BRANCH overrides). Never fails the build.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
OUT="$REPO_ROOT/service/_build_stamp.json"

sha="unknown"
short="unknown"
branch="unknown"
version="0.0.0-dev"

# Release version comes from the repo-root VERSION file, the ONE place it is written
# (2026-08-03). Before that, four components each declared their own: the service said
# 0.1.0 (a stale SERVICE_VERSION in .env), its own default said 4.0.0, the bridge said
# 4.0.0 in seven hand-copied places, and the dashboard said 0.1.0 — while the actual
# releases were v0.1, v0.1.1, v0.1.2. None of them had ever been bumped by a release.
# Baking it into the stamp is the same trick already used for the sha: the container has
# no .git and no repo root, but service/ IS copied into the image.
if [ -n "${AIFY_VERSION:-}" ]; then
  version="$AIFY_VERSION"
elif [ -r "$REPO_ROOT/VERSION" ]; then
  # First non-empty, non-comment line; trailing CR stripped so a CRLF checkout on Windows
  # does not bake "0.1.2\r" into the stamp and out through the API.
  _v="$(grep -v '^[[:space:]]*#' "$REPO_ROOT/VERSION" | grep -v '^[[:space:]]*$' | head -1 | tr -d '\r' | xargs 2>/dev/null || echo "")"
  [ -n "$_v" ] && version="$_v"
fi

# Env overrides win (useful for CI where .git may be absent), then git.
if [ -n "${GIT_SHA:-}" ]; then
  sha="$GIT_SHA"
  short="${GIT_SHA:0:7}"
elif command -v git >/dev/null 2>&1 && git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  sha="$(git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null || echo unknown)"
  short="$(git -C "$REPO_ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
fi

if [ -n "${GIT_BRANCH:-}" ]; then
  branch="$GIT_BRANCH"
elif command -v git >/dev/null 2>&1 && git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  branch="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
fi

built_at="$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "")"

# Escape backslash + double-quote so a branch name containing them can't produce
# invalid JSON (git allows `"` and `\` in branch names; env overrides are unvalidated).
_json_escape() { printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g'; }
sha="$(_json_escape "$sha")"
short="$(_json_escape "$short")"
branch="$(_json_escape "$branch")"
built_at="$(_json_escape "$built_at")"
version="$(_json_escape "$version")"

cat > "$OUT" <<EOF
{"sha":"$sha","short":"$short","branch":"$branch","built_at":"$built_at","version":"$version"}
EOF

echo "stamp.sh: wrote $OUT (version=$version sha=$short branch=$branch)"
