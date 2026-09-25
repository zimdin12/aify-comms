#!/bin/bash
# bump-version.sh X.Y.Z — write one release version into every file that must carry it.
#
# `VERSION` is the one source. These copies exist because a consumer cannot read it: the bridge has no
# repo root once installed (`mcp/stdio/version.js`), npm reads its own manifests, and a Claude plugin
# reads `.claude-plugin/plugin.json`. `test_version_single_source.py` and
# `mcp/stdio/tests/version-consistency.test.js` fail when any of them disagree, so a missed file is a
# red test rather than a silent lie. The service reads it from the build stamp: run `scripts/stamp.sh`
# before building.

set -euo pipefail

version="${1:-}"
if ! [[ "$version" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "usage: bash scripts/bump-version.sh X.Y.Z" >&2
  exit 2
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

printf '%s\n' "$version" > VERSION
sed -i -E "s/^export const AIFY_VERSION = \"[^\"]+\";/export const AIFY_VERSION = \"$version\";/" mcp/stdio/version.js
grep -q "^export const AIFY_VERSION = \"$version\";" mcp/stdio/version.js || {
  echo "mcp/stdio/version.js has no AIFY_VERSION line to rewrite" >&2
  exit 1
}

# The JSON files are rewritten by node, which keeps every other key and the file's own indentation.
node - "$version" <<'JS'
const fs = require("fs");
const version = process.argv[2];
const edit = (file, apply) => {
  const data = JSON.parse(fs.readFileSync(file, "utf8"));
  apply(data);
  fs.writeFileSync(file, JSON.stringify(data, null, 2) + "\n");
};
edit("mcp/stdio/package.json", (d) => { d.version = version; });
edit("mcp/stdio/package-lock.json", (d) => {
  d.version = version;
  if (d.packages && d.packages[""]) d.packages[""].version = version;
});
edit(".claude-plugin/plugin.json", (d) => { d.version = version; });
JS

echo "bump-version.sh: $version written. Next: run the suites, bash scripts/stamp.sh, rebuild, re-run install.sh."
