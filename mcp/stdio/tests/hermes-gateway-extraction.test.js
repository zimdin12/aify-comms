// Proves the gateway extraction from hermes-managed-host.js was a pure file split, and exercises the moved
// surface directly.
//
// Two obligations, and they are different. The reconstruction below proves nothing MOVED that was not
// declared — put the 23 extracted spans back, undo the import edits, and require byte-identity with a
// tracked pre-slice fixture. The unit tests after it prove the moved code still answers correctly, and they
// deliberately assert things `hermes-managed-host.test.js` does not: 26 of the 27 host exports already had
// executing importers, so a new file full of assertions the old suite already makes would add a file and no
// evidence.
//
// The prover lives in service/new_dashboard/extraction-proof.mjs because that is where it was built for the
// app.js lane. It is imported across trees rather than copied — a second copy of a proof is how two proofs
// come to disagree about what they prove.

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  declarationSpan,
  reconstruct,
} from "../../../service/new_dashboard/extraction-proof.mjs";

import {
  MAX_REENSURE_WITHOUT_RECOVERY,
  gatewayIndexUrlFromWs,
  gatewayUnreachableMessage,
  isGatewayConnectRefused,
  nextReEnsureBudget,
  shouldApplyGatewayTurnEnd,
  sleep,
} from "../hermes-gateway.mjs";
import { HERMES_CMD, MACHINE_ID, RUNTIME } from "../hermes-env.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const read = (rel) => fs.readFileSync(path.join(HERE, "..", rel), "utf-8");

const PRISTINE = "fixtures/hermes-managed-host.before-gateway.js";

const GATEWAY = "hermes-gateway.mjs";
const ENV = "hermes-env.mjs";

/** Indices are 0-based positions in the PRISTINE fixture, measured from it. */
const EXTRACTIONS = [
  {
    module: ENV,
    items: [
      { name: "MACHINE_ID", at: 104, marker: "// MACHINE_ID moved to ./hermes-env.mjs in v0.5.4." },
      { name: "RUNTIME", at: 164, marker: "// RUNTIME moved to ./hermes-env.mjs in v0.5.4." },
      { name: "HERMES_CMD", at: 198, marker: "// HERMES_CMD moved to ./hermes-env.mjs in v0.5.4." },
    ],
  },
  {
    module: GATEWAY,
    items: [
      { name: "READY_TIMEOUT_MS", at: 120, marker: "// READY_TIMEOUT_MS moved to ./hermes-gateway.mjs in v0.5.4." },
      { name: "RPC_TIMEOUT_MS", at: 121, marker: "// RPC_TIMEOUT_MS moved to ./hermes-gateway.mjs in v0.5.4." },
      { name: "GATEWAY_PROBE_TIMEOUT_MS", at: 246, marker: "// GATEWAY_PROBE_TIMEOUT_MS moved to ./hermes-gateway.mjs in v0.5.4." },
      { name: "sleep", at: 251, marker: "// sleep moved to ./hermes-gateway.mjs in v0.5.4." },
      { name: "scrapeToken", at: 375, marker: "// scrapeToken moved to ./hermes-gateway.mjs in v0.5.4." },
      { name: "waitForIndexToken", at: 389, marker: "// waitForIndexToken moved to ./hermes-gateway.mjs in v0.5.4." },
      { name: "ensureGatewayHost", at: 429, marker: "// ensureGatewayHost moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
      { name: "MAX_REENSURE_WITHOUT_RECOVERY", at: 658, marker: "// MAX_REENSURE_WITHOUT_RECOVERY moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
      { name: "nextReEnsureBudget", at: 663, marker: "// nextReEnsureBudget moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
      { name: "maybeReEnsureGatewayHost", at: 669, marker: "// maybeReEnsureGatewayHost moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
      { name: "openGatewayWsClient", at: 812, marker: "// openGatewayWsClient moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
      { name: "isGatewayConnectRefused", at: 979, marker: "// isGatewayConnectRefused moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
      { name: "gatewayUnreachableMessage", at: 1002, marker: "// gatewayUnreachableMessage moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
      { name: "reportGatewayDead", at: 1037, marker: "// reportGatewayDead moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
      { name: "gatewayIndexUrlFromWs", at: 1067, marker: "// gatewayIndexUrlFromWs moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
      { name: "makeGatewayReachabilityProbe", at: 1086, marker: "// makeGatewayReachabilityProbe moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
      { name: "shouldApplyGatewayTurnEnd", at: 1717, marker: "// shouldApplyGatewayTurnEnd moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
      { name: "_teardownState", at: 1864, marker: "// _teardownState moved to ./hermes-gateway.mjs in v0.5.4." },
      { name: "teardownGatewayHost", at: 1868, marker: "// teardownGatewayHost moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
      { name: "installShutdownTeardown", at: 1930, marker: "// installShutdownTeardown moved to ./hermes-gateway.mjs in v0.5.4.", pristineExported: true },
    ],
  },
];

const MODULES = () => ({ [GATEWAY]: read(GATEWAY), [ENV]: read(ENV) });

// The two import edits this slice made to the host, undone by the prover.
const IMPORT_EDITS = [
  {
    added: 'import { HERMES_CMD, MACHINE_ID, RUNTIME } from "./hermes-env.mjs";  // v0.5.4: neutral owner',
  },
];

function hostWithoutSliceImports() {
  const lines = read("hermes-managed-host.js").split(String.fromCharCode(10));
  for (const edit of IMPORT_EDITS) {
    const at = lines.indexOf(edit.added);
    assert.notEqual(at, -1, `import line not found verbatim: ${edit.added}`);
    lines.splice(at, 1);
  }
  // The gateway import is a multi-line block; find and drop it as a unit.
  const open = lines.findIndex((l) => l.startsWith("import {  // v0.5.4: moved out"));
  assert.notEqual(open, -1, "the gateway import block must be present verbatim");
  let close = open;
  while (close < lines.length && !lines[close].startsWith('} from "./hermes-gateway.mjs";')) close += 1;
  assert.ok(close < lines.length, "the gateway import block must be terminated");
  lines.splice(open, close - open + 1);
  return lines.join(String.fromCharCode(10));
}

test("hermes-managed-host.js reconstructs byte-identically from the two extracted modules", () => {
  const rebuilt = reconstruct({
    after: hostWithoutSliceImports(),
    modules: MODULES(),
    extractions: EXTRACTIONS,
  });
  assert.equal(
    rebuilt,
    read(PRISTINE),
    "reconstruction differs from the pre-slice host, so the split changed something outside its declared spans",
  );
});

test("the reconstruction fixture is TRACKED, not ignored", () => {
  // A proof that needs .git does not run from `git archive`; a fixture matched by .gitignore does not exist
  // on a clean clone. Both mistakes have shipped in this repo before.
  const full = path.join(HERE, "..", PRISTINE);
  assert.ok(fs.existsSync(full), `${PRISTINE} must exist beside the bridge`);
});

test("every declared pristineExported item really is exported in its module", () => {
  // The prover cross-checks this too, but asserting it here names the offender instead of failing a 3,000
  // line byte comparison.
  for (const step of EXTRACTIONS) {
    const source = MODULES()[step.module];
    for (const item of step.items) {
      const span = declarationSpan(source, item.name);
      assert.ok(span, `${item.name} not found in ${step.module}`);
      if (item.pristineExported) {
        assert.match(span.text, /^export\s/, `${item.name} is declared pristineExported but is not exported`);
      }
    }
  }
});

// ---------------------------------------------------------------- moved surface, executed

test("gatewayIndexUrlFromWs converts a ws URL to the http index it scrapes", () => {
  assert.equal(gatewayIndexUrlFromWs("ws://127.0.0.1:8123/ws"), "http://127.0.0.1:8123/");
  assert.equal(gatewayIndexUrlFromWs("wss://host:9/ws"), "https://host:9/");
});

test("gatewayIndexUrlFromWs returns empty for input it cannot convert", () => {
  // The caller uses the result as a URL; '' is checked, a malformed string would be fetched.
  for (const value of ["", null, undefined, "not a url"]) {
    assert.equal(gatewayIndexUrlFromWs(value), "", `unexpected for ${String(value)}`);
  }
});

test("isGatewayConnectRefused recognises the refusal shapes a dead gateway produces", () => {
  assert.equal(isGatewayConnectRefused(new Error("connect ECONNREFUSED 127.0.0.1:8123")), true);
  assert.equal(isGatewayConnectRefused({ code: "ECONNREFUSED" }), true);
});

test("isGatewayConnectRefused does NOT claim an unrelated error is a refusal", () => {
  // A false positive here reports a live gateway dead and tears it down.
  assert.equal(isGatewayConnectRefused(new Error("socket hang up")), false);
  assert.equal(isGatewayConnectRefused(null), false);
  assert.equal(isGatewayConnectRefused(undefined), false);
});

test("nextReEnsureBudget spends on a re-ensure and refills on a recovery", () => {
  assert.equal(nextReEnsureBudget(3, { reEnsured: true }), 2, "a re-ensure costs one");
  assert.equal(nextReEnsureBudget(1, { recovered: true }), MAX_REENSURE_WITHOUT_RECOVERY, "recovery refills");
  assert.equal(nextReEnsureBudget(2, {}), 2, "neither event leaves it alone");
});

test("nextReEnsureBudget never goes below zero", () => {
  // The budget gates a relaunch loop; a negative would keep comparing as truthy-negative and relaunch forever.
  assert.equal(nextReEnsureBudget(0, { reEnsured: true }), 0);
  assert.equal(nextReEnsureBudget(-5, { reEnsured: true }), 0);
});

test("gatewayUnreachableMessage names the gateway URL so the operator can check it", () => {
  const msg = gatewayUnreachableMessage("ws://127.0.0.1:8123/ws");
  assert.match(msg, /8123/, "the message must carry the port that failed");
  assert.equal(typeof msg, "string");
});

test("shouldApplyGatewayTurnEnd suppresses a turn-end only while a dispatch turn is open and unobserved", () => {
  // I guessed a (sessionA, sessionB) signature and wrote a passing-looking test for a function that takes
  // ONE object. Reading it was the fix. The real rule: a gateway turn-end applies unless a dispatch turn is
  // open and no working state has been observed yet — that window is where an early turn-end would close a
  // run the agent has not actually started.
  assert.equal(shouldApplyGatewayTurnEnd({ dispatchTurnOpen: true, observedWorking: false }), false,
    "an open dispatch turn with nothing observed must NOT be ended by the gateway");
  assert.equal(shouldApplyGatewayTurnEnd({ dispatchTurnOpen: true, observedWorking: true }), true,
    "once working has been observed the turn-end is real");
  assert.equal(shouldApplyGatewayTurnEnd({ dispatchTurnOpen: false }), true,
    "with no dispatch turn open there is nothing to protect");
  assert.equal(shouldApplyGatewayTurnEnd(), true, "the default argument must not suppress a turn-end");
  assert.equal(shouldApplyGatewayTurnEnd({}), true);
});

test("sleep resolves after roughly the requested delay", async () => {
  const started = Date.now();
  await sleep(20);
  assert.ok(Date.now() - started >= 15, "sleep must actually wait");
});

// ---------------------------------------------------------------- the neutral env module

test("hermes-env exposes the identity constants both sides read", () => {
  assert.equal(RUNTIME, "hermes", "the runtime name is what agents are registered under");
  assert.equal(typeof HERMES_CMD, "string");
  assert.ok(HERMES_CMD.length > 0, "an empty hermes command would spawn nothing");
  assert.equal(typeof MACHINE_ID, "string");
  assert.ok(MACHINE_ID.length > 0, "an empty machine id breaks same-host claim matching");
});

/** The modules a file IMPORTS — parsed, not grepped. */
function importedModules(source) {
  return [...source.matchAll(/^import\s[\s\S]*?from\s*"([^"]+)"\s*;/gm)].map((m) => m[1]);
}

test("hermes-env imports neither the gateway nor the host", () => {
  // The whole reason this module exists: a constant with readers on both sides must live in neither.
  //
  // Asserted on parsed IMPORTS, not on the file text. My first version grepped for the substring and failed
  // against correct code, because both new modules NAME hermes-managed-host.js in their header comments
  // explaining what they were extracted from. Substring-versus-structure, for the umpteenth time in this
  // series: a mention is not a dependency.
  const mods = importedModules(read(ENV));
  assert.ok(!mods.some((m) => m.includes("hermes-gateway")), `env imports the gateway: ${mods}`);
  assert.ok(!mods.some((m) => m.includes("hermes-managed-host")), `env imports the host: ${mods}`);
});

test("hermes-gateway does not import the host it was extracted from", () => {
  // The dependency inversion this series exists to prevent, asserted rather than assumed.
  const mods = importedModules(read(GATEWAY));
  assert.ok(!mods.some((m) => m.includes("hermes-managed-host")), `gateway imports the host: ${mods}`);
  assert.ok(mods.some((m) => m.includes("hermes-env")), "the gateway must take its identity constants from the neutral module");
});
