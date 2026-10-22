// The hermes host census agrees with its table: every effect, shared state and import edge in the code a hermes
// host runs has a class and an owner, and nothing new appears unclassified. This is the H2 prerequisite of the
// hermes-host-to-env plan (page 2, "Step one"): the table is the move list.
//
// THE PARSER is the acorn Node bundles, so the census runs in a child with `--expose-internals`. It loads or the
// test FAILS: a census that skips when it cannot parse would pass on every host that lacks the parser.
//
// THE MUTANTS keep every export unchanged, because an exported-name table stays green exactly when a private
// closure or an import edge changes owner. Each one must make the census disagree with the table.

import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const CLI = path.join(STDIO, "tests", "hermes-census-cli.mjs");

function run(args, input) {
  const result = spawnSync(process.execPath, ["--expose-internals", CLI, ...args], {
    input, encoding: "utf8", maxBuffer: 64 * 1024 * 1024,
  });
  assert.equal(result.status, 0, `the census could not run, so nothing was checked:\n${result.stderr}`);
  return JSON.parse(result.stdout);
}
const tree = (overrides) => run(overrides ? ["--stdin"] : [], overrides ? JSON.stringify(overrides) : undefined);
const one = (source) => run(["--one"], source);
const source = (file) => readFileSync(path.join(STDIO, file), "utf8");

/** `file` with `text` inserted after `anchor`, which must occur exactly once. */
function inserted(file, anchor, text) {
  const original = source(file);
  assert.equal(original.split(anchor).length, 2, `${file}: the mutant's anchor must occur once: ${anchor}`);
  return original.replace(anchor, anchor + text);
}

const baseline = tree();

test("the census runs on Node's own acorn", () => {
  assert.match(baseline.acorn, /^\d+\.\d+/, "no acorn version came back");
});

test("every occurrence in the hermes host population has a class and an owner, and nothing is unpinned", () => {
  assert.deepEqual(baseline.unaccounted, []);
});

test("CONTROL: the census finds the private spawn closure and the delivery loop's service calls", () => {
  const host = baseline.counts["hermes-managed-host.js"];
  assert.equal(host["process|spawn|runEnsureHostCli/spawnJoiningLease"], 1, "spawnJoiningLease's spawn was not found");
  const loop = Object.keys(baseline.counts["hermes-delivery-loop.mjs"]);
  assert.ok(loop.some((key) => key.startsWith("service-http|")), "no service-HTTP call found in the delivery loop");
});

test("CONTROL: a source with no effects reads absent, and each grammar row is recognised", () => {
  assert.deepEqual(one("export function add(a, b) { return a + b; }\n").occurrences, []);
  const kinds = one([
    'import { spawn } from "node:child_process";',
    'import { makeAifyHttpCall } from "./aify-http.mjs";',
    'import { start } from "./liveness-heartbeat.js";',
    'import fs from "node:fs";',
    "let count = 0;",
    "const seen = new Map();",
    "export function go(name) {",
    "  const http = makeAifyHttpCall(process.env.AIFY_URL, 'k');",
    "  http('GET', '/x'); spawn('x'); setTimeout(() => {}, 1); new WebSocket('ws://x');",
    "  fs.writeFileSync('a', 'b'); process.on('exit', () => {}); child.kill();",
    "  return import(name);",
    "}",
  ].join("\n")).occurrences.map((o) => o.kind);
  for (const kind of ["import", "heartbeat", "shared-state", "env-read", "service-http", "process", "timer",
    "ws-open", "file-write", "signal", "kill", "UNKNOWN"]) {
    assert.ok(kinds.includes(kind), `the grammar row ${kind} was not recognised`);
  }
});

test("an import() of a non-literal is UNKNOWN, and an UNKNOWN fails the census", () => {
  const mutated = tree({ "hermes-session-id.js": `${source("hermes-session-id.js")}\nexport async function load(n) { return import(n); }\n` });
  assert.ok(mutated.unaccounted.some((line) => /UNKNOWN\|import\(\) of a non-literal/.test(line)), mutated.unaccounted.join("\n"));
});

const MUTANTS = [
  {
    name: "a new private spawn inside a non-exported closure",
    file: "hermes-managed-host.js",
    anchor: "  const spawnJoiningLease = (...args) => {\n",
    text: "    const sideSpawn = () => spawn(\"hermes\", [\"--version\"]);\n    void sideSpawn;\n",
    expect: /process\|spawn\|runEnsureHostCli\/spawnJoiningLease\/sideSpawn/,
  },
  {
    name: "a new aify-http call",
    file: "hermes-delivery-loop.mjs",
    anchor: 'import { startLivenessHeartbeat } from "./liveness-heartbeat.js";\n',
    text: 'function extraServiceCall() { return makeAifyHttpCall("http://127.0.0.1:1", ""); }\nvoid extraServiceCall;\n',
    expect: /service-http\|makeAifyHttpCall\|extraServiceCall/,
  },
  {
    name: "a new top-level Map",
    file: "hermes-gateway-protocol.js",
    anchor: "",
    text: "const pendingByAgent = new Map();\nvoid pendingByAgent;\n",
    expect: /shared-state\|Map\|<module>/,
  },
];

for (const mutant of MUTANTS) {
  test(`MUTANT, exports unchanged: ${mutant.name} is not accounted for`, () => {
    const mutatedSource = mutant.anchor ? inserted(mutant.file, mutant.anchor, mutant.text) : `${mutant.text}${source(mutant.file)}`;
    const mutated = tree({ [mutant.file]: mutatedSource });
    assert.deepEqual(mutated.exports[mutant.file], baseline.exports[mutant.file], "the mutant changed the exports");
    assert.ok(mutated.unaccounted.some((line) => mutant.expect.test(line)), `not caught:\n${mutated.unaccounted.join("\n")}`);
  });
}
