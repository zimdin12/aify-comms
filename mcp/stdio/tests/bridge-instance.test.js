// Which bridge process this is, and the two properties 46 readers assume of it.
//
// `BRIDGE_INSTANCE_ID` keys the `bridge_instances` table, attributes heartbeats and claims, and is how the
// service decides which bridge currently OWNS an environment. When a second bridge starts for the same
// environment, this value is what distinguishes the newcomer from the one being superseded — and the reaping
// that follows a supersede targets the superseded bridge's workers.
//
// UNIQUENESS ACROSS PROCESSES and STABILITY WITHIN ONE are therefore both load-bearing, and neither was
// tested: this lived in `server.js`, the bin entry point, which nothing imports.
//
// The failure mode if either broke is the reason to test it rather than read it. A per-call generator would
// give every reader a DIFFERENT id while each individual value still looked like a valid uuid — nothing
// would throw, registration would succeed, and the bridge's heartbeats would be attributed to instances that
// had never registered. A constant shared across processes would credit one bridge's claims to another.

import assert from "node:assert/strict";
import test from "node:test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

import { BRIDGE_INSTANCE_ID } from "../bridge-instance.mjs";
import { declaringModules } from "./bridge-sources.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const LEAF = pathToFileURL(path.join(STDIO, "bridge-instance.mjs")).href;

// Read the id out of a fresh process, N times per process so within-process stability is observable too.
function idsFromChild(reads = 1) {
  const out = execFileSync(
    process.execPath,
    ["--input-type=module", "-e",
      "const ids = [];"
      + " for (let i = 0; i < " + reads + "; i += 1) {"
      + "   const m = await import(" + JSON.stringify(LEAF) + ");"
      + "   ids.push(m.BRIDGE_INSTANCE_ID);"
      + " }"
      + " process.stdout.write(JSON.stringify(ids));"],
    { encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"] },
  );
  return JSON.parse(out);
}

test("it is a uuid-shaped string", () => {
  // Shape matters because it reaches URL path segments and a database key. Asserted as the real v4 shape
  // rather than "is a non-empty string", which would pass for a counter.
  assert.equal(typeof BRIDGE_INSTANCE_ID, "string");
  assert.match(
    BRIDGE_INSTANCE_ID,
    /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    "must be a v4 uuid — this value is a database key and a URL segment",
  );
});

test("STABLE within a process: every import in one process sees the same id", () => {
  // The property heartbeats depend on. A bridge registers under this id and its heartbeats must carry the
  // same one minutes later, or the service sees beats from an instance that never registered.
  //
  // DOCUMENTS AN ESM GUARANTEE RATHER THAN GUARDING CODE, and I would rather say so than let it read as a
  // guard. A module body runs once per process and both `import` calls resolve to the same instance — I
  // verified that directly — so this assertion cannot fail for any implementation that puts the value at
  // module scope. I tried to write a mutant that re-mints per read; `export const { X } = obj` destructures
  // once, so the getter fired once and the value was still stable. The mutant was invalid, not the code
  // wrong. What IS guardable here is uniqueness across processes and non-derivation, below, and both bite.
  const ids = idsFromChild(5);
  assert.equal(ids.length, 5);
  assert.equal(new Set(ids).size, 1, `five reads in one process must agree, got ${JSON.stringify(ids)}`);
});

test("STABLE across repeated reads in THIS process too", () => {
  // Same caveat as above: this records the contract readers rely on, not a property a mutation can break.
  const first = BRIDGE_INSTANCE_ID;
  assert.equal(BRIDGE_INSTANCE_ID, first);
  assert.equal(BRIDGE_INSTANCE_ID, first, "reading it must not change it");
});

test("UNIQUE across processes: two bridges must be distinguishable", () => {
  // The property the supersede logic depends on. Two bridges sharing an id would have one's claims credited
  // to the other, and the reap that follows a supersede would target the wrong worker set.
  const runs = [idsFromChild()[0], idsFromChild()[0], idsFromChild()[0]];
  assert.equal(new Set(runs).size, 3, `three processes must produce three ids, got ${JSON.stringify(runs)}`);
  for (const id of runs) {
    assert.notEqual(id, BRIDGE_INSTANCE_ID, "…and none may match this process's own id");
  }
});

test("it is not derived from anything an operator can collide", () => {
  // A machine name, a pid, or a timestamp would all collide in the situations that matter most: two bridges
  // started on the same host, in the same second, for the same environment. `randomUUID` is the point.
  const src = readFileSync(path.join(STDIO, "bridge-instance.mjs"), "utf-8");
  assert.match(src, /randomUUID\(\)/, "the id must be random, not derived");
  assert.doesNotMatch(src, /process\.(pid|ppid)|hostname|Date\.now|MACHINE_ID/,
    "no host, pid or clock input — those collide exactly when it matters");
});

test("exactly one module declares it, and the bridge still reads it", () => {
  assert.deepEqual(
    declaringModules("BRIDGE_INSTANCE_ID"), [{ file: "bridge-instance.mjs", kind: "binding" }],
    "a second declaration would give the bridge two identities and silently split its attribution",
  );
  const server = readFileSync(path.join(STDIO, "server.js"), "utf-8");
  assert.match(server, /(?<![\w.])BRIDGE_INSTANCE_ID(?![\w])/, "server.js is still expected to read it");
});

test("the owner holds nothing else", () => {
  const src = readFileSync(path.join(STDIO, "bridge-instance.mjs"), "utf-8");
  const imports = [...src.matchAll(/^import .* from "([^"]+)";$/gm)].map((m) => m[1]);
  assert.deepEqual(imports, ["crypto"], "one import: the uuid source");
  assert.equal((src.match(/^export /gm) || []).length, 1, "one export");
  assert.doesNotMatch(src, /^let\s/m, "no mutable state — the id must not be reassignable");
});
