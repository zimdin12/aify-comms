// The small helpers and tuning constants the hermes host modules export, executed.
//
// These assert things `hermes-managed-host.test.js` does not: the URL conversion, the connect-refusal
// classifier, the re-ensure budget, the turn-end suppression rule, the /dispatch/claim error
// classifier, the relations the delivery timings depend on, and that the gateway and env modules keep
// the dependency direction they were split out to have (neither imports the host).

import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import {
  MAX_REENSURE_WITHOUT_RECOVERY,
  gatewayIndexUrlFromWs,
  gatewayUnreachableMessage,
  isGatewayConnectRefused,
  nextReEnsureBudget,
  shouldApplyGatewayTurnEnd,
} from "../hermes-gateway.mjs";
import { HERMES_CMD, MACHINE_ID, RUNTIME } from "../hermes-env.mjs";
import { ATTACH_POLL_MS, ATTACH_WAIT_MS } from "../hermes-active-session.mjs";
import { classifyClaimError } from "../hermes-delivery-run.mjs";
import { isGatewaySessionWorking } from "../hermes-gateway-protocol.js";
import { DEFAULT_IDLE_DEBOUNCE_TICKS, makeGatewayTurnDetector } from "../hermes-gateway-turn-detector.js";
import { REPULSE_MS, REPULSE_WINDOW_MS } from "../hermes-inflight.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const read = (rel) => fs.readFileSync(path.join(HERE, "..", rel), "utf-8");

const GATEWAY = "hermes-gateway.mjs";
const ENV = "hermes-env.mjs";

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

// ---------------------------------------------------------------- delivery timings and classifiers

test("classifyClaimError: a 410 is terminal at once, a 404 only past its grace, anything else resets", () => {
  // A 410 means the agent was removed on purpose. A 404 is also seen while the service restarts, so
  // it ends the loop only after a run of them; any other answer proves the agent exists again.
  assert.deepEqual(classifyClaimError({ status: 410 }), { terminal: true, reason: "agent-removed" });
  const counter = { count: 0 };
  assert.deepEqual(classifyClaimError({ status: 404 }, counter, { grace: 2 }), { terminal: false });
  assert.deepEqual(classifyClaimError({ status: 404 }, counter, { grace: 2 }),
    { terminal: true, reason: "agent-removed" });
  const reset = { count: 1 };
  assert.deepEqual(classifyClaimError({ status: 503 }, reset, { grace: 2 }), { terminal: false });
  assert.equal(reset.count, 0, "a non-404 answer must reset the 404 run");
  assert.deepEqual(classifyClaimError(new Error("socket hang up")), { terminal: false });
});

test("the attach poll is shorter than the attach deadline, so a cold start gets more than one look", () => {
  assert.ok(ATTACH_POLL_MS > 0 && ATTACH_WAIT_MS > 0);
  assert.ok(ATTACH_POLL_MS < ATTACH_WAIT_MS, `poll ${ATTACH_POLL_MS}ms >= wait ${ATTACH_WAIT_MS}ms`);
});

test("the re-pulse window is bounded and never shorter than one re-pulse", () => {
  // The window is what stops a missed completion from holding `working` for ever.
  assert.ok(Number.isFinite(REPULSE_WINDOW_MS), "an unbounded window can latch working");
  assert.ok(REPULSE_MS <= REPULSE_WINDOW_MS);
});

test("the turn detector ends a turn after exactly DEFAULT_IDLE_DEBOUNCE_TICKS idle reads", () => {
  assert.ok(Number.isInteger(DEFAULT_IDLE_DEBOUNCE_TICKS) && DEFAULT_IDLE_DEBOUNCE_TICKS > 0);
  const detector = makeGatewayTurnDetector();
  assert.equal(detector.observe("working"), "start");
  for (let i = 1; i < DEFAULT_IDLE_DEBOUNCE_TICKS; i += 1) {
    assert.equal(detector.observe("idle"), null, `ended early, after ${i} idle read(s)`);
  }
  assert.equal(detector.observe("idle"), "end");
});

test("isGatewaySessionWorking reads only a working status as working", () => {
  assert.equal(isGatewaySessionWorking("working"), true);
  assert.equal(isGatewaySessionWorking("  Working "), true);
  for (const status of ["idle", "starting", "", null, undefined]) {
    assert.equal(isGatewaySessionWorking(status), false, String(status));
  }
});
