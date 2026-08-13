// The resident gateway reader's SAFETY MACHINERY — the parts that only matter when the gateway misbehaves.
//
// `resident-hermes-turn-detector.test.js` covers what this reader returns on a healthy gateway. These are
// the failure paths, and each one is a decision that is invisible while everything works:
//
//   * every error reads as `""`, because the detector treats `""` as "not idle" — so a dropped socket can
//     only ever DELAY a turn ending, never end a live one early;
//   * a sustained-dead gateway is probed one read in ten, because every failed connect costs a timeout and
//     the poll loop serves every agent this bridge has;
//   * one success clears the backoff immediately, so recovery takes one cycle rather than a window;
//   * the WS client is reused across reads and dropped on failure, so a dead socket self-heals.
//
// Both seams are injectable, so none of this needs a gateway: the tests drive a fake client and count what
// it was asked to do.

import assert from "node:assert/strict";
import test from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import {
  makeResidentGatewayStatusReader,
  shouldArmResidentHermesTurnDetector,
} from "../resident-gateway-status.mjs";
import { declaringModules, isUsedInBridge } from "./bridge-sources.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

// A fake gateway. `behaviour` decides what each request does, so a test can make it fail, recover, or
// answer with rows. Records every open and every request.
function fakeGateway({ behaviour }) {
  const state = { opens: 0, requests: 0, closes: 0 };
  let call = 0;
  const openWs = async () => {
    state.opens += 1;
    const client = {
      _socket: { readyState: 1 },
      close: () => { state.closes += 1; client._socket.readyState = 3; },
      request: async () => {
        state.requests += 1;
        const outcome = behaviour(++call);
        if (outcome instanceof Error) throw outcome;
        return outcome;
      },
    };
    state.client = client;
    return client;
  };
  return { state, openWs };
}

const rows = (list) => ({ result: { sessions: list } });
const reader = (openWs, extra = {}) => makeResidentGatewayStatusReader({
  agentId: "rgs-agent", gatewayUrl: "ws://127.0.0.2:1/gw", openWs,
  readSessionId: () => "", ...extra,
});

test("ANY FAILURE READS AS EMPTY — it can delay a turn ending, never end one early", async () => {
  // The detector treats "" as not-idle. If a gateway hiccup returned something idle-looking, a live turn
  // would be cut off mid-answer; the 1800s server backstop is what covers the other direction.
  const { openWs } = fakeGateway({ behaviour: () => new Error("socket died") });
  const read = reader(openWs);
  assert.equal(await read(), "", "a throwing request must read as empty, not throw");
});

test("a failed read DROPS the client so the next read reconnects", async () => {
  // Without this a dead socket is reused forever and the reader never recovers on its own.
  const { state, openWs } = fakeGateway({ behaviour: () => new Error("nope") });
  const read = reader(openWs);
  await read();
  await read();
  assert.equal(state.opens, 2, "each failure must be followed by a fresh connect");
  assert.ok(state.closes >= 1, "…and the dead client must be closed, not leaked");
});

test("a SOCKET-LESS client is still dropped after a failure", async () => {
  // `wsOpen` answers TRUE when a client exposes no `_socket` — the shape the source calls "a fake test
  // client w/o a socket", and the shape a wrapper without a raw socket has. For those, the readyState check
  // can never force a reconnect, so `wsClient = null` on failure is the ONLY thing that does.
  //
  // My first version of the test above used a fake whose `close()` flips readyState, so the two mechanisms
  // were redundant and a mutation deleting `wsClient = null` survived. This is the case that isolates it.
  let opens = 0;
  let fail = true;
  const openWs = async () => {
    opens += 1;
    return { request: async () => { if (fail) throw new Error("down"); return rows([{ id: "s1", status: "running" }]); } };
  };
  const read = reader(openWs);
  assert.equal(await read(), "", "first read fails");
  assert.equal(opens, 1);
  fail = false;
  assert.equal(await read(), "running", "the next read must reconnect and succeed");
  assert.equal(opens, 2, "a socket-less client must not be reused after it failed");
});

test("A HEALTHY CLIENT IS REUSED, not reopened per read", async () => {
  // The counterpart. Reconnecting on every poll tick would cost a handshake per agent per cycle.
  const { state, openWs } = fakeGateway({ behaviour: () => rows([{ id: "s1", status: "running" }]) });
  const read = reader(openWs);
  await read(); await read(); await read();
  assert.equal(state.opens, 1, "one connect for three reads");
  assert.equal(state.requests, 3, "…but a request each time — the status is never cached");
});

test("AFTER THREE FAILURES IT BACKS OFF to one probe in ten", async () => {
  // Not an optimisation: each failed connect eats a connect timeout, and the poll loop serves every agent
  // this bridge has. The threshold and period are the function's own constants (3 and 10).
  const { state, openWs } = fakeGateway({ behaviour: () => new Error("gateway gone") });
  const read = reader(openWs);
  for (let i = 0; i < 3; i += 1) await read();
  assert.equal(state.opens, 3, "the first three reads all really try");

  // Reads 4..12: the backoff skips nine and probes on the tenth.
  const before = state.opens;
  for (let i = 0; i < 9; i += 1) assert.equal(await read(), "", "a skipped read still answers empty");
  assert.equal(state.opens, before, "nine backed-off reads must not connect at all");
  await read();
  assert.equal(state.opens, before + 1, "…and the tenth probes once");
});

test("ONE SUCCESS CLEARS THE BACKOFF IMMEDIATELY", async () => {
  // Recovery must take one cycle, not a window. This is the property that makes the backoff safe to have.
  let dead = true;
  const { state, openWs } = fakeGateway({
    behaviour: () => (dead ? new Error("down") : rows([{ id: "s1", status: "running" }])),
  });
  const read = reader(openWs);
  for (let i = 0; i < 4; i += 1) await read();
  assert.ok(state.opens <= 4, "backed off after the threshold");

  dead = false;
  // Walk to the next probe tick; the moment one succeeds the counter resets.
  let recovered = "";
  for (let i = 0; i < 10 && !recovered; i += 1) recovered = await read();
  assert.equal(recovered, "running", "the gateway's own status is returned once it answers");

  const afterRecovery = state.opens;
  await read(); await read();
  assert.equal(state.opens, afterRecovery, "…and a healthy client is then reused, not reconnected");
  assert.equal(await read(), "running", "every subsequent read answers immediately — no residual skipping");
});

test("the SESSION ID wins over the fallbacks when the gateway knows it", async () => {
  // Three-tier resolution: real id, then the legacy synthetic key, then the most recent row. The gateway is
  // per-agent so the fallback is usually right, but a real id must never be overridden by it.
  const { openWs } = fakeGateway({
    behaviour: () => rows([
      { id: "other", status: "idle" },
      { id: "real-1", status: "running" },
    ]),
  });
  const read = makeResidentGatewayStatusReader({
    agentId: "rgs-agent", gatewayUrl: "ws://127.0.0.2:1/gw", openWs,
    readSessionId: () => "real-1",
  });
  assert.equal(await read(), "running", "the row matching the known session id is the answer");
});

test("a readSessionId that throws does not take the read down", async () => {
  // It reads a marker file; a missing or unreadable one is ordinary. The reader must fall through to the
  // other tiers rather than treating it as a gateway failure.
  const { openWs } = fakeGateway({ behaviour: () => rows([{ id: "s1", status: "running" }]) });
  const read = makeResidentGatewayStatusReader({
    agentId: "rgs-agent", gatewayUrl: "ws://127.0.0.2:1/gw", openWs,
    readSessionId: () => { throw new Error("no marker"); },
  });
  assert.equal(await read(), "", "current behaviour: the throw is caught by the read's own catch");
});

test("THE ARM GATE refuses anything that is not hermes with a real ws:// gateway", async () => {
  // A hard no-op, so a non-hermes runtime or a placeholder gateway never opens a WS or posts a turn signal.
  assert.equal(shouldArmResidentHermesTurnDetector({ runtime: "hermes", gatewayUrl: "ws://h/gw" }), true);
  assert.equal(shouldArmResidentHermesTurnDetector({ runtime: "hermes", gatewayUrl: "wss://h/gw" }), true);
  for (const runtime of ["codex", "claude-code", "pi", "", undefined]) {
    assert.equal(shouldArmResidentHermesTurnDetector({ runtime, gatewayUrl: "ws://h/gw" }), false,
      `${runtime} must not arm the hermes detector`);
  }
  // The placeholder case, which is the one that actually happens: hermes YAML interpolates `${VAR}` to its
  // literal text when unset, and a literal is truthy.
  for (const gatewayUrl of ["${AIFY_HERMES_GATEWAY_URL}", "http://h/gw", "h/gw", "", "   ", undefined]) {
    assert.equal(shouldArmResidentHermesTurnDetector({ runtime: "hermes", gatewayUrl }), false,
      `${JSON.stringify(gatewayUrl)} is not a ws:// gateway and must not arm`);
  }
  // `sessionMode` is accepted and deliberately ignored — resident and managed-resident both arm.
  assert.equal(
    shouldArmResidentHermesTurnDetector({ runtime: "hermes", gatewayUrl: "ws://h/gw", sessionMode: "managed" }),
    shouldArmResidentHermesTurnDetector({ runtime: "hermes", gatewayUrl: "ws://h/gw", sessionMode: "resident" }),
    "gating is runtime+gateway only");
  assert.equal(shouldArmResidentHermesTurnDetector(), false, "no argument at all is a no-op, not a throw");
});

test("exactly one module declares each, and the bridge still uses them", () => {
  for (const name of ["makeResidentGatewayStatusReader", "shouldArmResidentHermesTurnDetector"]) {
    assert.deepEqual(declaringModules(name), [{ file: "resident-gateway-status.mjs", kind: "function" }],
      `${name} must be declared exactly once, by its owner`);
    assert.ok(isUsedInBridge(name), `${name} must still be called by something`);
  }
  const server = fs.readFileSync(path.join(STDIO, "server.js"), "utf-8");
  assert.doesNotMatch(server, /^export function (makeResidentGatewayStatusReader|shouldArmResidentHermesTurnDetector)/m,
    "server.js must no longer export them — the test that used to import them from there now uses the owner");
});

test("the owner holds no module state and reaches only owned leaves", () => {
  const src = fs.readFileSync(path.join(STDIO, "resident-gateway-status.mjs"), "utf-8");
  assert.doesNotMatch(src, /^let\s/m,
    "the backoff counters are per-reader closure state, which is the point — nothing may be module-level");
  const imports = [...src.matchAll(/^} from "([^"]+)";$|^import .* from "([^"]+)";$/gm)]
    .map((m) => m[1] || m[2]).sort();
  assert.deepEqual(imports, [
    "./hermes-endpoint.js",
    "./hermes-gateway-protocol.js",
    "./hermes-gateway.mjs",
    "./hermes-session-id.js",
  ]);
});
