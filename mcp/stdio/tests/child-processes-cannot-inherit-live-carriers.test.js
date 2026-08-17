#!/usr/bin/env node
// A test that spawns a child must not let it inherit the live agent's identity, service or session.
//
// THREE REVIEW ROUNDS FAILED ON THIS SHAPE, each in a different place, each green on a machine where the
// carrier happened to be unset:
//   1. a Python test read the operator's real hermes session id (HERMES_TUI_ACTIVE_SESSION_FILE unsealed);
//   2. 17 bridge tests had their fake servers bypassed because the modules read CLAUDE_MCP_SERVER_URL FIRST,
//      and 12 more sent the operator's real API key;
//   3. a CHILD process inherited AIFY_HERMES_GATEWAY_URL and the active-session file, because its parent passed
//      the env map into the wrong parameter and silently overrode nothing.
//
// The first two are guarded (hermes_carriers.py's source-derived list; env-carrier-pairs-are-sealed-together).
// This one guards the third: a child's environment is built by the parent, the parent's own seals do not reach
// it, and the failure is invisible wherever the carrier is unset.
//
// THE RULE. A test file that spreads `process.env` into a spawned child must obtain that env from
// `_child-env.mjs`'s `sealedChildEnv`, which DELETES every live carrier. Spreading `{ ...process.env }` straight
// into `spawn` is what this gate refuses.
//
// EXEMPTIONS ARE LISTED, NOT INFERRED. Some children are supposed to inherit the ambient environment — that is
// the thing under test. Each one is named here with its reason, so the list is a decision record rather than a
// silent hole, and it can only shrink by argument.

import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

import { LIVE_ENV_CARRIERS, sealedChildEnv, leakedCarriers } from "./_child-env.mjs";

const TESTS = fileURLToPath(new URL(".", import.meta.url));

// Files that deliberately hand a child the ambient environment. The reason is the point: a name with no reason
// is an unguarded hole waiting to be found by a reviewer instead of by this gate.
const EXEMPT = new Map([
  ["aify-service-endpoint.test.js",
   "IS_REMOTE resolves at module load, so the child is spawned precisely to observe how the AMBIENT env "
   + "decides it — sealing the carriers would delete the subject."],
  ["environment-identity.test.js",
   "asserts how a child DERIVES its environment identity from inherited variables; the inheritance is the "
   + "behaviour under test."],
  ["doctor-process-readers.test.js",
   "reads a child's own /proc environ back out; it must see what a real process would carry."],
]);

const SPREAD = /\{\s*\.\.\.process\.env/;
const SPAWNS = /\b(spawn|spawnSync|fork|execFile|execFileSync)\s*\(/;

const files = readdirSync(TESTS).filter((n) => n.endsWith(".test.js") || n.endsWith(".test.mjs")).sort();
const SELF = "child-processes-cannot-inherit-live-carriers.test.js";

const offenders = [];
for (const name of files) {
  if (name === SELF) continue;
  const source = readFileSync(join(TESTS, name), "utf-8");
  if (!SPREAD.test(source) || !SPAWNS.test(source)) continue;
  if (source.includes("sealedChildEnv")) continue;   // uses the helper
  if (EXEMPT.has(name)) continue;
  offenders.push(name);
}

assert.deepEqual(offenders, [], [
  "These test files spread `{ ...process.env }` into a spawned child without going through",
  "`sealedChildEnv` from ./_child-env.mjs, so in a live wrapper environment the child inherits the",
  "operator's service URL, API key, hermes session and agent identity. That passes on any machine where",
  "those happen to be unset — which is every developer shell and no wrapper — and it has now reached a",
  "reviewer three times.",
  "",
  "Fix: `import { sealedChildEnv } from \"./_child-env.mjs\"` and pass `env: sealedChildEnv({ ...what the",
  "child SHOULD see })`. If the inheritance is the thing under test, add the file to EXEMPT with the reason.",
  "",
  "Offenders:",
  ...offenders.map((n) => `  - ${n}`),
].join("\n"));

// ── the helper's own guarantees ─────────────────────────────────────────────

// Anti-vacuity: with every carrier SET, a sealed env must carry none of them.
const hostile = {};
for (const name of LIVE_ENV_CARRIERS) hostile[name] = `live-value-${name}`;
const saved = new Map(LIVE_ENV_CARRIERS.map((n) => [n, process.env[n]]));
try {
  Object.assign(process.env, hostile);
  const sealed = sealedChildEnv();
  assert.deepEqual(leakedCarriers(sealed), [],
    "sealedChildEnv left a carrier in place — the helper, not the callers, is broken");
  // …and it must still pass ordinary variables through, or callers would quietly lose PATH.
  assert.equal(sealed.PATH, process.env.PATH, "the sealed env dropped PATH");
  // …and `extra` must be able to re-add one deliberately, which is how a fake service URL is supplied.
  const withFake = sealedChildEnv({ AIFY_SERVER_URL: "http://127.0.0.2:1" });
  assert.equal(withFake.AIFY_SERVER_URL, "http://127.0.0.2:1", "extra could not re-add a carrier");
  // …and `undefined` in `extra` deletes rather than stringifies, so a caller can drop anything else.
  assert.equal("PATH" in sealedChildEnv({ PATH: undefined }), false, "extra:undefined did not delete");
} finally {
  for (const [name, value] of saved) {
    if (value === undefined) delete process.env[name];
    else process.env[name] = value;
  }
}

// Every aliased pair the OTHER gate knows about must also be in this list: a child that inherits the legacy
// name is the same defect as a test that seals only the new one.
for (const pair of [["AIFY_SERVER_URL", "CLAUDE_MCP_SERVER_URL"], ["AIFY_API_KEY", "CLAUDE_MCP_API_KEY"]]) {
  for (const name of pair) {
    assert.ok(LIVE_ENV_CARRIERS.includes(name), `${name} is read as an alias but is not sealed from children`);
  }
}

console.log(
  `child-processes-cannot-inherit-live-carriers.test.js: ${files.length} test files scanned, `
  + `${LIVE_ENV_CARRIERS.length} carriers sealed, ${EXEMPT.size} documented exemption(s)`,
);
