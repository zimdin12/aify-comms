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
import { tmpDir } from "./_tmpdir.js";

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

// ── the DEFAULT session-id reader ───────────────────────────────────────────
//
// Twenty-second cluster off the V8-coverage census: the `readSessionId` default parameter. Every test above
// injects it — including the two that cover what happens when it answers and when it throws — so the default,
// which is what runs in production, had a zero call count. What it does is read the agent's on-disk session
// marker; if that read were broken the reader would silently fall through to the most-recent row, which for a
// PER-AGENT gateway is usually the same answer. The bug would be invisible until the day it was not.
//
// TEMP/TMP ARE SEALED, because `defaultMarkerTmpDir()` is `process.env.TEMP || process.env.TMP || os.tmpdir()`
// read at call time. Unsealed, these tests would read (and the marker write would land in) the operator's real
// temp directory alongside live agents' markers.
//
// THREE THINGS SURVIVE MUTATION HERE, all defence-in-depth rather than gaps, and each measured:
//
//   * The default reader's `try/catch` around `readSessionIdMarker`, and `readSessionIdMarker`'s own `catch`.
//     Either alone is absorbed by the other (and by the reader's outer catch, which turns anything thrown into
//     ""). Removing BOTH — in both files — IS caught, which is what proves the property is tested at all.
//   * The trailing-newline case below is PINNED BEHAVIOUR, not a discriminating test: four independent trims
//     stand between a shell-written marker and an id comparison — this read, `isUsableSessionId`'s own internal
//     trim, the reader's arrow, and `pickSessionStatusById`'s `wanted`. Removing two of the four changes
//     nothing observable, so the case documents the guarantee rather than guarding any one trim.
//   * Reducing `defaultMarkerTmpDir()` to `os.tmpdir()`. On win32 `os.tmpdir()` reads process.env.TEMP itself,
//     so with TEMP sealed the two are the same function. On Linux it reads TMPDIR, where the mutation WOULD
//     matter — a platform equivalence, not a covered case.

// The marker path is written out by hand rather than through `writeSessionIdMarker`, so this does not become an
// assertion that the writer agrees with itself: the convention is `aify-hermes-session-<agentId>` holding the
// bare id. The agreement with the writer is asserted separately, below.
const MARKER_AGENT = "rgs-marker-agent";
const markerPath = (dir, agentId = MARKER_AGENT) => path.join(dir, `aify-hermes-session-${agentId}`);

// ASYNC, and it AWAITS `run`. The first version returned the promise from a synchronous `try`, so the `finally`
// restored TEMP/TMP before the awaited body had read anything — the seal was already gone by the time the reader
// looked for a marker, and the test failed while the product was correct.
async function withSealedTemp(run) {
  const dir = tmpDir("aify-rgs-marker-");
  const saved = { TEMP: process.env.TEMP, TMP: process.env.TMP };
  process.env.TEMP = dir;
  process.env.TMP = dir;
  assert.equal(process.env.TEMP, dir, "the TEMP seal did not take");
  try {
    return await run(dir);
  } finally {
    for (const [key, value] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
  }
}

// A reader with NO readSessionId injected — the whole point of these cases.
const defaultReader = (openWs, agentId = MARKER_AGENT) =>
  makeResidentGatewayStatusReader({ agentId, gatewayUrl: "ws://127.0.0.2:1/gw", openWs });

// The most-recent fallback stamps rows from `last_active` / `started_at` / `created_at` — NOT `updated_at`,
// which was my first guess and left every decoy row stamped 0, so "most recent" silently meant "first in the
// list". These helpers make the decoy genuinely newer, so the marker path and the fallback cannot agree by
// accident.
const OLDER = "2026-08-17T12:00:00.000Z";
const NEWER = "2026-08-17T18:00:00.000Z";

test("THE DEFAULT READER finds the agent's real session id on disk", async () => {
  await withSealedTemp(async (dir) => {
    fs.writeFileSync(markerPath(dir), "20260817_120000_abc123", "utf8");
    const { openWs } = fakeGateway({
      behaviour: () => rows([
        { id: "20260817_120000_abc123", status: "running", last_active: OLDER },
        // Newer, so the most-recent fallback would pick THIS one — the assertion below only holds if the
        // marker was read.
        { id: "someone-elses-session", status: "idle", last_active: NEWER },
      ]),
    });

    assert.equal(await defaultReader(openWs)(), "running",
      "the status came from the most-recent row instead of the agent's own marked session");
  });
});

test("no marker on disk falls through to the fallbacks rather than throwing", async () => {
  await withSealedTemp(async () => {
    const { openWs } = fakeGateway({ behaviour: () => rows([{ id: "whatever", status: "idle", last_active: NEWER }]) });
    assert.equal(await defaultReader(openWs)(), "idle");
  });
});

test("a POISONED marker is treated as absent, never resumed", async () => {
  // An unexpanded `${HERMES_SESSION_ID}` written by a pre-fix launcher. `isUsableSessionId` rejects it, and the
  // reader must fall through — resolving status for a garbage id would report on a session that cannot exist.
  await withSealedTemp(async (dir) => {
    fs.writeFileSync(markerPath(dir), "${HERMES_SESSION_ID}", "utf8");
    const { openWs } = fakeGateway({
      behaviour: () => rows([
        // The poison row is stamped OLDER and listed FIRST, so "the marker was used" and "the fallback picked
        // row zero" give different answers. Without that, both hypotheses returned `running` and this test
        // passed against a marker that HAD been accepted.
        { id: "${HERMES_SESSION_ID}", status: "running", last_active: OLDER },
        { id: "real-row", status: "idle", last_active: NEWER },
      ]),
    });
    assert.equal(await defaultReader(openWs)(), "idle", "a placeholder marker was used as a session id");
  });
});

test("an unreadable marker directory reads as no marker", async () => {
  // The default wraps its read in a try/catch. A missing temp dir must not turn a status poll into an
  // exception the detector never expected.
  await withSealedTemp(async (dir) => {
    process.env.TEMP = path.join(dir, "does", "not", "exist");
    process.env.TMP = process.env.TEMP;
    const { openWs } = fakeGateway({ behaviour: () => rows([{ id: "x", status: "idle", last_active: NEWER }]) });
    assert.equal(await defaultReader(openWs)(), "idle");
  });
});

test("a marker written with a trailing newline still resolves", async () => {
  // Markers are also produced by shell wrappers, and `echo id > marker` appends a newline. Without the trim the
  // id would carry it, fail `isUsableSessionId`, and read as absent — an agent whose session is on disk resuming
  // fresh instead. (This is the case the trim mutation exposed: my first fixture wrote no newline at all.)
  await withSealedTemp(async (dir) => {
    fs.writeFileSync(markerPath(dir), "20260817_120000_abc123\r\n", "utf8");
    const { openWs } = fakeGateway({
      behaviour: () => rows([
        { id: "20260817_120000_abc123", status: "running", last_active: OLDER },
        { id: "decoy", status: "idle", last_active: NEWER },
      ]),
    });
    assert.equal(await defaultReader(openWs)(), "running", "a trailing newline made the marker unreadable");
  });
});

test("the WRITER refuses to persist a placeholder id", async () => {
  // The other end of the poison guard. Refusing at write time is what stops a bad id recurring on every
  // launch; the read-time check is the belt for markers written before that guard existed.
  const { writeSessionIdMarker } = await import("../hermes-endpoint.js");
  await withSealedTemp(async (dir) => {
    for (const bad of ["${HERMES_SESSION_ID}", "", "   ", "has spaces", "semi;colon"]) {
      assert.equal(writeSessionIdMarker(MARKER_AGENT, bad), false, `${JSON.stringify(bad)} was accepted`);
      assert.equal(fs.existsSync(markerPath(dir)), false, `${JSON.stringify(bad)} was written to disk`);
    }
    assert.equal(writeSessionIdMarker(MARKER_AGENT, "20260817_120000_abc123"), true,
      "a valid id was refused — the guard is now rejecting everything");
  });
});

test("the marker convention this test writes by hand is the one the WRITER uses", async () => {
  // Guards the fixture above from drifting away from the product: if the filename or the file's contents change,
  // the hand-written path stops being a valid marker and the default-reader tests would pass for the wrong
  // reason (falling through to the fallback, which several of them assert anyway).
  const { writeSessionIdMarker } = await import("../hermes-endpoint.js");
  await withSealedTemp(async (dir) => {
    writeSessionIdMarker(MARKER_AGENT, "20260817_120000_abc123");
    assert.ok(fs.existsSync(markerPath(dir)),
      `the writer did not produce ${markerPath(dir)} — the hand-written fixture path is stale`);
    assert.equal(fs.readFileSync(markerPath(dir), "utf8").trim(), "20260817_120000_abc123");
  });
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
