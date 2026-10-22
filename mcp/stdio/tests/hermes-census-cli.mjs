// Prints the hermes host census as JSON. Needs Node's bundled acorn, so it runs with `--expose-internals`:
//
//   node --expose-internals tests/hermes-census-cli.mjs            the census of the tree
//   node --expose-internals tests/hermes-census-cli.mjs --stdin    the same, with { "<file>": "<source>" } overrides
//                                                                  read from stdin (how the test builds mutants)
//   node --expose-internals tests/hermes-census-cli.mjs --one      one source on stdin: its occurrences and exports
//
// Output: { acorn, counts, exports, via, edges, owners, unaccounted }. `unaccounted` is empty when the table and the
// tree agree.

import { createRequire } from "node:module";
import { readFileSync } from "node:fs";
import path from "node:path";

import { STDIO_DIR } from "./bridge-sources.mjs";
import { census, censusOf, countsOf, ownerOf, resolveRelative, unaccounted } from "./hermes-census.mjs";

const require = createRequire(import.meta.url);
const acorn = require("internal/deps/acorn/acorn/dist/acorn");
const walk = require("internal/deps/acorn/acorn-walk/dist/walk");

if (process.argv.includes("--one")) {
  process.stdout.write(JSON.stringify({ acorn: acorn.version, ...censusOf(readFileSync(0, "utf8"), { parse: acorn.parse, walk }) }));
  process.exit(0);
}

const overrides = process.argv.includes("--stdin") ? JSON.parse(readFileSync(0, "utf8")) : {};
const table = JSON.parse(readFileSync(new URL("./hermes-census-table.json", import.meta.url), "utf8"));

const read = (file) => {
  if (file in overrides) return overrides[file];
  for (const candidate of [file, `${file}.js`, `${file}.mjs`]) {
    try { return readFileSync(path.join(STDIO_DIR, candidate), "utf8"); } catch { /* next */ }
  }
  return null;
};

const files = census({ read, parse: acorn.parse, walk });

// Each occurrence's owner, and each edge that crosses owners: the seams the move has to cut.
const owners = {};
const edges = [];
for (const [file, { occurrences }] of files) {
  for (const o of occurrences) {
    const owner = ownerOf(table, file, o.symbol)?.owner ?? "UNMAPPED";
    owners[owner] = (owners[owner] ?? 0) + 1;
    if (o.kind !== "import") continue;
    const target = resolveRelative(file, o.detail);
    if (!target) continue;
    const to = files.has(target) ? target : [`${target}`].find((t) => files.has(t));
    const targetOwner = table.files[to]?.owner ?? "MIXED";
    if (owner !== targetOwner) edges.push({ from: file, symbol: o.symbol, owner, to, targetOwner });
  }
}

process.stdout.write(JSON.stringify({
  acorn: acorn.version,
  counts: countsOf(files),
  exports: Object.fromEntries([...files].map(([f, r]) => [f, r.exports])),
  via: Object.fromEntries([...files].map(([f, r]) => [f, r.via])),
  edges,
  owners,
  unaccounted: unaccounted(files, table),
}, null, 1));
