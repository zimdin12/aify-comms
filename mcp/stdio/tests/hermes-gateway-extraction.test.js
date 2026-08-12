// Proves EVERY extraction from hermes-managed-host.js was a pure file split, and exercises the moved surface.
//
// ONE PROOF PER TARGET FILE, not per slice — the app.js lane's shape, and this file learned it the hard way.
// It began as the gateway slice's receipt, pinning the exact env import line the gateway added. The very next
// slice added TMP_DIR to that line and this proof went red: correct detection, wrong granularity. A per-slice
// proof is only runnable until the next slice touches the same lines. So both slices are declared below and
// each new one appends.
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
const SESSION = "hermes-active-session.mjs";
const REPORTING = "hermes-run-reporting.mjs";
const INFLIGHT = "hermes-inflight.mjs";
const AIFYHTTP = "aify-http.mjs";

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
  {
    module: SESSION,
    items: [
      { name: "ATTACH_WAIT_MS", at: 128, marker: "// ATTACH_WAIT_MS moved to ./hermes-active-session.mjs in v0.5.4." },
      { name: "ATTACH_POLL_MS", at: 129, marker: "// ATTACH_POLL_MS moved to ./hermes-active-session.mjs in v0.5.4." },
      { name: "ATTACH_FRESH_GRACE_FRACTION", at: 158, marker: "// ATTACH_FRESH_GRACE_FRACTION moved to ./hermes-active-session.mjs in v0.5.4." },
      { name: "activeListRowsLocal", at: 258, marker: "// activeListRowsLocal moved to ./hermes-active-session.mjs in v0.5.4." },
      { name: "rowFreshnessStamp", at: 274, marker: "// rowFreshnessStamp moved to ./hermes-active-session.mjs in v0.5.4." },
      { name: "rowRealIdLocal", at: 291, marker: "// rowRealIdLocal moved to ./hermes-active-session.mjs in v0.5.4." },
      { name: "stampForSessionId", at: 311, marker: "// stampForSessionId moved to ./hermes-active-session.mjs in v0.5.4." },
      { name: "sessionKeyFor", at: 325, marker: "// sessionKeyFor moved to ./hermes-active-session.mjs in v0.5.4." },
      { name: "ensureStableSession", at: 755, marker: "// ensureStableSession moved to ./hermes-active-session.mjs in v0.5.4.", pristineExported: true },
      { name: "defaultWriteActiveSessionFile", at: 300, marker: "// defaultWriteActiveSessionFile moved to ./hermes-active-session.mjs in v0.5.4." },
      { name: "waitForActiveSession", at: 1155, marker: "// waitForActiveSession moved to ./hermes-active-session.mjs in v0.5.4.", pristineExported: true },
      { name: "startResumeMarkerSync", at: 2034, marker: "// startResumeMarkerSync moved to ./hermes-active-session.mjs in v0.5.4.", pristineExported: true },
      { name: "runResolveSessionCli", at: 2791, marker: "// runResolveSessionCli moved to ./hermes-active-session.mjs in v0.5.4.", pristineExported: true },
    ],
  },
  {
    module: AIFYHTTP,
    items: [
      { name: "coerceLoopbackToIPv4", at: 95, marker: "// coerceLoopbackToIPv4 moved to ./aify-http.mjs in v0.5.4." },
      { name: "AIFY_SERVER_URL", at: 99, marker: "// AIFY_SERVER_URL moved to ./aify-http.mjs in v0.5.4." },
      { name: "AIFY_API_KEY", at: 102, marker: "// AIFY_API_KEY moved to ./aify-http.mjs in v0.5.4." },
      { name: "HTTP_TIMEOUT_MS", at: 119, marker: "// HTTP_TIMEOUT_MS moved to ./aify-http.mjs in v0.5.4." },
      { name: "makeAifyHttpCall", at: 342, marker: "// makeAifyHttpCall moved to ./aify-http.mjs in v0.5.4." },
    ],
  },
  {
    module: INFLIGHT,
    items: [
      { name: "REPULSE_MS", at: 174, marker: "// REPULSE_MS moved to ./hermes-inflight.mjs in v0.5.4." },
      { name: "TURN_START_TIMEOUT_MS", at: 175, marker: "// TURN_START_TIMEOUT_MS moved to ./hermes-inflight.mjs in v0.5.4." },
      { name: "REPULSE_WINDOW_MS", at: 194, marker: "// REPULSE_WINDOW_MS moved to ./hermes-inflight.mjs in v0.5.4." },
      { name: "fetchRunStatus", at: 919, marker: "// fetchRunStatus moved to ./hermes-inflight.mjs in v0.5.4." },
      { name: "makeInFlightProbe", at: 1595, marker: "// makeInFlightProbe moved to ./hermes-inflight.mjs in v0.5.4.", pristineExported: true },
      { name: "makeInFlightPulse", at: 1731, marker: "// makeInFlightPulse moved to ./hermes-inflight.mjs in v0.5.4.", pristineExported: true },
    ],
  },
  {
    module: REPORTING,
    items: [
      { name: "CHANNEL_BRIDGE_PREFIX", at: 110, marker: "// CHANNEL_BRIDGE_PREFIX moved to ./hermes-run-reporting.mjs in v0.5.4 with its only reader." },
      { name: "channelBridgeId", at: 111, marker: "// channelBridgeId moved to ./hermes-run-reporting.mjs in v0.5.4." },
      { name: "reportTurnBusy", at: 895, marker: "// reportTurnBusy moved to ./hermes-run-reporting.mjs in v0.5.4." },
      { name: "clearTurn", at: 904, marker: "// clearTurn moved to ./hermes-run-reporting.mjs in v0.5.4." },
      { name: "markRunDelivered", at: 933, marker: "// markRunDelivered moved to ./hermes-run-reporting.mjs in v0.5.4." },
      { name: "markRunFailed", at: 946, marker: "// markRunFailed moved to ./hermes-run-reporting.mjs in v0.5.4." },
      { name: "markRunRequeued", at: 1125, marker: "// markRunRequeued moved to ./hermes-run-reporting.mjs in v0.5.4." },
    ],
  },
  {
    module: ENV,
    items: [
      { name: "TMP_DIR", at: 163, marker: "// TMP_DIR moved to ./hermes-env.mjs in v0.5.4." },
      // resolveHermesPython went to the SESSION module first and moved here a slice later, once the reviewer
      // asked whether the members were session-identity or merely session-adjacent. The plan records where a
      // body lives NOW, not where it passed through — a plan that tracked the journey would have to be
      // rewritten every time an owner was corrected, which is the opposite of what it is for.
      { name: "resolveHermesPython", at: 726, marker: "// resolveHermesPython moved to ./hermes-active-session.mjs in v0.5.4.", pristineExported: true },
    ],
  },
];

const MODULES = () => ({
  [GATEWAY]: read(GATEWAY),
  [ENV]: read(ENV),
  [SESSION]: read(SESSION),
  [REPORTING]: read(REPORTING),
  [INFLIGHT]: read(INFLIGHT),
  [AIFYHTTP]: read(AIFYHTTP),
});

// Import lines the extractions added, in their CURRENT form. The env line is shared by both slices — the
// gateway created it, the session slice added TMP_DIR to it — so it is pinned once, as it stands now.
const IMPORT_EDITS = [
  {
    // The env import became a multi-line block when resolveHermesPython joined it, so it is pinned as one.
    addedBlock: [
      "import {  // v0.5.4: neutral owner",
      "  HERMES_CMD,",
      "  MACHINE_ID,",
      "  RUNTIME,",
      "  TMP_DIR,",
      "  resolveHermesPython,",
      '} from "./hermes-env.mjs";',
    ],
  },
];

function hostWithoutSliceImports() {
  const lines = read("hermes-managed-host.js").split(String.fromCharCode(10));
  for (const edit of IMPORT_EDITS) {
    const block = edit.addedBlock ?? [edit.added];
    const at = lines.indexOf(block[0]);
    assert.notEqual(at, -1, `import line not found verbatim: ${block[0]}`);
    for (let k = 1; k < block.length; k += 1) {
      assert.equal(lines[at + k], block[k], `import block line ${k} does not match`);
    }
    lines.splice(at, block.length);
  }
  // The gateway import is a multi-line block; find and drop it as a unit.
  // Find each block by its TERMINATOR and walk BACK to the opener. My first version took the first
  // `import {  // v0.5.4: moved out` line and assumed it belonged to the module it was looking for; the two
  // blocks are not in that order in the file, so it deleted the session block while hunting for the gateway
  // one and then could not find the session block it had just removed. Anchoring on the unique line is the
  // fix — the opener comment is identical for every slice and therefore cannot identify one.
  for (const from of [
    '} from "./hermes-gateway.mjs";',
    '} from "./hermes-active-session.mjs";',
    '} from "./hermes-run-reporting.mjs";',
    '} from "./hermes-inflight.mjs";',
    '} from "./aify-http.mjs";',
  ]) {
    const close = lines.findIndex((l) => l.startsWith(from));
    assert.notEqual(close, -1, `the import block ending in ${from} must be present verbatim`);
    let open = close;
    while (open >= 0 && !lines[open].startsWith("import {  // v0.5.4: moved out")) open -= 1;
    assert.ok(open >= 0, `no opener found for the block ending in ${from}`);
    lines.splice(open, close - open + 1);
  }
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
