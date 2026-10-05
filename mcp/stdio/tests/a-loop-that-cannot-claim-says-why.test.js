// The service already returns blockedBy. The loop must not discard its reason or print its payload.
import "./_sealed-temp.mjs";
import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import { runPollCycle } from "../hermes-delivery-run.mjs";
import { runDeliveryLoop } from "../hermes-delivery-loop.mjs";
import { makeAifyHttpCall } from "../aify-http.mjs";
import { tmpDir } from "./_tmpdir.js";

const SECRET = "fixture-private-value-never-log";
const MARKERS = tmpDir("aify-claim-diagnostics-");
process.on("exit", () => fs.rmSync(MARKERS, { recursive: true, force: true }));
const originalFetch = globalThis.fetch;
globalThis.fetch = async () => { throw new Error("fixture forbids unowned fetch"); };
process.on("exit", () => { globalThis.fetch = originalFetch; });

async function capture(action) {
  const saved = console.error;
  const lines = [];
  console.error = (...args) => lines.push(args.join(" "));
  try { return { result: await action(lines), lines }; }
  finally { console.error = saved; }
}

async function poll(response, state = { count: 0 }) {
  let calls = 0;
  const captured = await capture(() => runPollCycle({
    agentId: "fixture-claim-diagnostic", machineId: "fixture-machine", bridgeId: "fixture-bridge",
    claimErrorCounter: state, tempDir: MARKERS,
    httpCall: async (method, endpoint) => {
      calls++;
      assert.equal(method, "POST");
      assert.equal(endpoint, "/dispatch/claim");
      if (response instanceof Error) throw response;
      return response;
    },
    wsClient: { request() { assert.fail("a refused claim must never deliver"); } },
  }));
  assert.equal(calls, 1, "diagnostics must not add requests");
  assert.equal(captured.result.processed, 0);
  return captured;
}

// Derive this vocabulary from the producers, not a second test-owned list.
const reasons = [...new Set([
  "../../../service/api_core/claim_block_reason.py",
  "../../../service/api_core/claim_gating.py",
  "../../../service/dispatch_claim.py",
].flatMap((name) => [...fs.readFileSync(new URL(name, import.meta.url), "utf8")
  .matchAll(/"reason": "([a-z][a-z0-9_]{0,63})"/g)].map((m) => m[1])))];
assert.ok(reasons.includes("bridge_superseded"), "producer discovery did not engage");
for (const reason of reasons) {
  test(`a refused claim reports the service reason ${reason} without its payload`, async () => {
    const { result, lines } = await poll({ ok: true, run: null,
      blockedBy: { reason, hint: SECRET, body: SECRET, token: SECRET, ownerBridgeId: SECRET } });
    assert.equal(result.claimOk, true, "reporting must preserve the successful round-trip flag");
    assert.equal(lines.length, 1, "the silent blockedBy branch needs one diagnostic");
    assert.ok(lines[0].includes(reason), "the reported cause did not reach the operator");
    assert.ok(!lines.join("\n").includes(SECRET), "a claim payload leaked");
  });
}

test("a blocker without a code-shaped reason stays unknown and cannot echo free text", async () => {
  for (const reason of [undefined, null, 401, {}, "", SECRET, "token=private\nforged line", "Bridge_superseded", "4xx_error", "a".repeat(65), "bridge_superseded\n", "bridge_superseded\r\n"]) {
    const { lines } = await poll({ ok: true, run: null, blockedBy: { reason, hint: SECRET } });
    assert.equal(lines.length, 1);
    assert.match(lines[0], /blocked.*unknown/i);
    assert.ok(!lines[0].includes(SECRET));
    assert.ok(!lines[0].includes("forged line"));
  }
  const ownRun = await poll({ ok: true, run: null, blockedBy: { runId: "fixture-run", subject: SECRET } });
  assert.equal(ownRun.lines.length, 1);
  assert.match(ownRun.lines[0], /blocked_unknown/);
  assert.ok(!ownRun.lines[0].includes(SECRET));
});

test("new service codes are reported by their grammar without a bridge code list", async () => {
  for (const reason of ["a", "a".repeat(64), "future_gate_v2", "constructor"]) {
    const { result, lines } = await poll({ ok: true, run: null,
      blockedBy: { reason, hint: SECRET, token: SECRET } });
    assert.equal(result.claimOk, true);
    assert.equal(lines.length, 1);
    assert.ok(lines[0].includes(`${reason}; service blocked this claim`), "a new code was discarded");
    assert.ok(!lines[0].includes(SECRET));
  }
});

test("repeated blockers stay quiet until the reason changes or the block clears", async () => {
  const state = { count: 0 };
  const first = { ok: true, run: null, blockedBy: { reason: "bridge_superseded" } };
  assert.equal((await poll(first, state)).lines.length, 1);
  assert.equal((await poll(first, state)).lines.length, 0);
  assert.equal((await poll({ ok: true, run: null, blockedBy: { reason: "environment_not_online" } }, state)).lines.length, 1);
  const empty = await poll({ ok: true, run: null }, state);
  assert.equal(empty.lines.length, 1);
  assert.match(empty.lines[0], /no_run_returned/);
  assert.equal((await poll({ ok: true, run: null }, state)).lines.length, 0);
  assert.equal((await poll(first, state)).lines.length, 1);
});

test("a healthy empty claim does not invent a blocker", async () => {
  const { result, lines } = await poll({ ok: true, run: null });
  assert.deepEqual(result, { processed: 0, released: false, claimOk: true });
  assert.equal(lines.length, 1);
  assert.match(lines[0], /no_run_returned/);
  assert.match(lines[0], /no blocker reason/i);
});

test("an unsupported run mode is diagnosed without printing the run", async () => {
  const { lines } = await poll({ ok: true, run: { executionMode: "managed", body: SECRET } });
  assert.equal(lines.length, 1);
  assert.match(lines[0], /unsupported_execution_mode/);
  assert.ok(!lines[0].includes(SECRET));
});

test("a real HTTP authentication refusal reports status but never the response body", async () => {
  let calls = 0;
  const saved = globalThis.fetch;
  globalThis.fetch = async (url, options) => {
    calls++;
    assert.equal(url, "http://127.0.0.1:1/api/v1/dispatch/claim");
    assert.equal(options.method, "POST");
    return new Response(SECRET, { status: 401 });
  };
  try {
    const httpCall = makeAifyHttpCall("http://127.0.0.1:1", "");
    const { result, lines } = await capture(() => runPollCycle({
      agentId: "fixture-claim-diagnostic", httpCall, claimErrorCounter: { count: 0 }, tempDir: MARKERS,
    }));
    assert.equal(calls, 1, "logging must not replay a refused POST");
    assert.equal(result.claimOk, false);
    assert.equal(lines.length, 1);
    assert.ok(!lines[0].includes(SECRET), "the transport's raw body reached stderr");
    assert.match(lines[0], /HTTP 401.*authentication refused/i);
  } finally { globalThis.fetch = saved; }
});

test("transport and unclassified claim errors are reported without arbitrary error messages", async () => {
  for (const [error, expected] of [
    [Object.assign(new Error(SECRET), { cause: { code: "ECONNREFUSED" } }), /ECONNREFUSED/],
    [Object.assign(new Error(SECRET), { name: "AbortError" }), /request_aborted_or_timed_out/],
    [new Error(SECRET), /unknown_claim_error/],
  ]) {
    const { result, lines } = await poll(error);
    assert.equal(result.claimOk, false);
    assert.equal(lines.length, 1);
    assert.match(lines[0], expected);
    assert.ok(!lines[0].includes(SECRET));
  }
});

test("404 grace and 410 terminal handling remain unchanged", async () => {
  const state = { count: 0 };
  const error = Object.assign(new Error(SECRET), { status: 404 });
  assert.equal((await poll(error, state)).result.terminal, undefined);
  assert.equal((await poll(error, state)).result.terminal, undefined);
  const terminal = await poll(error, state);
  assert.equal(terminal.result.terminal, "agent-removed");
  assert.match(terminal.lines.join("\n"), /TERMINAL/);
  assert.ok(!terminal.lines.join("\n").includes(SECRET));
  const removed = await poll(Object.assign(new Error(SECRET), { status: 410 }));
  assert.equal(removed.result.terminal, "agent-removed");
});

async function loop(serverUrl) {
  let fetches = 0;
  let claims = 0;
  let spawns = 0;
  let loggedBeforeGateway = false;
  const out = await capture((lines) => runDeliveryLoop("fixture-claim-diagnostic", {
    serverUrl, markerDir: MARKERS, maxIterations: 2, sleepImpl: async () => {},
    spawnImpl: () => { spawns++; assert.fail("the fixture gateway must be reused"); },
    fetchImpl: async () => {
      if (fetches++ === 0) loggedBeforeGateway = (serverUrl ? /started.*waiting for gateway/ : /service_url_missing/).test(lines[0] || "");
      return { ok: true, text: async () => '<script>window.__HERMES_SESSION_TOKEN__="fixture-token";</script>' };
    },
    openWs: async () => ({ request: async () => ({ result: { sessions: [{ id: "fixture-session", status: "ready" }] } }), close() {} }),
    installTeardown() {}, startLivenessHeartbeat: () => () => {}, startEffort: () => () => {},
    httpCall: async (method, endpoint) => {
      if (endpoint === "/dispatch/claim") { claims++; return { ok: true, run: null }; }
      assert.match(endpoint, /claimer-lease$/);
      return { ok: true };
    }, writeReady() {}, clearReady() {},
  }));
  assert.equal(spawns, 0);
  assert.ok(fetches > 0, "the real loop never reached the injected gateway");
  assert.ok(loggedBeforeGateway, "the startup diagnostic must precede the gateway await");
  return { ...out, claims };
}

test("the loop logs before its gateway wait so a started helper is not a zero-byte log", async () => {
  const out = await loop("http://127.0.0.1:1");
  assert.equal(out.claims, 2);
  assert.match(out.lines[0] || "", /started.*waiting for gateway.*dispatch\/claim/i);
});

test("the loop without a service URL reports why claims are disabled once", async () => {
  const out = await loop("");
  assert.equal(out.claims, 0);
  assert.equal(out.lines.length, 1);
  assert.match(out.lines[0], /service_url_missing.*claims disabled/i);
});
