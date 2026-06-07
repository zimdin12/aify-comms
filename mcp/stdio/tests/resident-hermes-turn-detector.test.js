#!/usr/bin/env node
// Resident-hermes turn-END via the gateway detector (status-accuracy Task 1).
//
// A RESIDENT hermes registers a gatewayUrl but ran NO turn-state detector, so its
// turn never ended — up to 30 min of false `working` after every turn (the worst
// single status inaccuracy). This wires the SAME continuous gateway turn detector
// the managed delivery loop uses into the resident bridge path, so a resident
// hermes ends its turn on sustained gateway idle (and re-stamps turn-busy while the
// gateway stays working). These are PURE unit tests of the wiring helpers — no real
// PTYs, no real WS: shouldArmResidentHermesTurnDetector gates arming, and the
// detector is fed a STUBBED readGatewayStatus + stubbed posts.
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  shouldArmResidentHermesTurnDetector,
  makeResidentGatewayStatusReader,
} from "../server.js";
import { startHermesGatewayTurnDetector } from "../hermes-gateway-turn-detector.js";

// ---------------------------------------------------------------------------
// shouldArmResidentHermesTurnDetector — the arm gate. Only a hermes runtime with
// a non-empty ws:// gatewayUrl arms the detector; everything else is a no-op so
// non-hermes / no-gateway residents never open a WS or post turn signals.
// ---------------------------------------------------------------------------

test("shouldArmResidentHermesTurnDetector: hermes + non-empty gatewayUrl arms (resident)", () => {
  assert.equal(
    shouldArmResidentHermesTurnDetector({
      runtime: "hermes",
      sessionMode: "resident",
      gatewayUrl: "ws://127.0.0.1:9100/api/ws?token=abc",
    }),
    true,
  );
});

test("shouldArmResidentHermesTurnDetector: hermes + non-empty gatewayUrl arms (managed-resident)", () => {
  assert.equal(
    shouldArmResidentHermesTurnDetector({
      runtime: "hermes",
      sessionMode: "managed-resident",
      gatewayUrl: "wss://gw.example/api/ws?token=abc",
    }),
    true,
  );
});

test("shouldArmResidentHermesTurnDetector: non-hermes runtime never arms", () => {
  for (const runtime of ["claude-code", "codex", "pi", "opencode", "", undefined]) {
    assert.equal(
      shouldArmResidentHermesTurnDetector({
        runtime,
        sessionMode: "resident",
        gatewayUrl: "ws://127.0.0.1:9100/api/ws?token=abc",
      }),
      false,
      `runtime=${String(runtime)} must not arm`,
    );
  }
});

test("shouldArmResidentHermesTurnDetector: empty / non-ws gatewayUrl never arms", () => {
  for (const gatewayUrl of ["", "   ", undefined, null, "http://127.0.0.1:9100", "${AIFY_HERMES_GATEWAY_URL}"]) {
    assert.equal(
      shouldArmResidentHermesTurnDetector({ runtime: "hermes", sessionMode: "resident", gatewayUrl }),
      false,
      `gatewayUrl=${JSON.stringify(gatewayUrl)} must not arm`,
    );
  }
});

// ---------------------------------------------------------------------------
// makeResidentGatewayStatusReader — mirror of readManagedSessionStatus
// (hermes-managed-host.js). Opens (lazily) a gateway WS, requests
// session.active_list, and resolves THIS agent's session status by real id →
// session-key → most-recent-row fallback. All collaborators are injected so the
// test never opens a real WS.
// ---------------------------------------------------------------------------

// A fake WS client. `respond` may be a static active_list response object or a
// per-request function (so the e2e test can advance the gateway status each tick).
function fakeWsClient(respond) {
  return {
    request: async () => (typeof respond === "function" ? respond() : respond),
    close() {},
    _socket: { readyState: 1 },
  };
}

test("makeResidentGatewayStatusReader: matches the agent's real session by id (preferred)", async () => {
  const resp = {
    result: {
      sessions: [
        { session_id: "real-123", title: "my task", status: "working", started_at: "2026-06-07T10:00:02Z" },
        { session_id: "other-999", title: "noise", status: "idle", started_at: "2026-06-07T10:00:01Z" },
      ],
    },
  };
  let opened = 0;
  const read = makeResidentGatewayStatusReader({
    agentId: "alice",
    gatewayUrl: "ws://127.0.0.1:9100/api/ws?token=x",
    openWs: async () => { opened++; return fakeWsClient(resp); },
    readSessionId: () => "real-123",
  });
  assert.equal(await read(), "working", "status resolved by the agent's real session id");
  assert.equal(opened, 1, "opened the gateway WS once (lazily, then reused)");
  // Second read reuses the same open client (no re-open).
  await read();
  assert.equal(opened, 1, "reuses the open WS client across reads");
});

test("makeResidentGatewayStatusReader: falls back to most-recent row when id/key miss", async () => {
  // Gateway keys rows by ephemeral runtime id + human title (never the durable
  // session_key), so a resumed session misses both lookups → most-recent row wins.
  const resp = {
    result: {
      sessions: [
        { session_id: "eph-1", title: "older", status: "idle", started_at: "2026-06-07T10:00:01Z" },
        { session_id: "eph-2", title: "newest", status: "working", started_at: "2026-06-07T10:00:03Z" },
      ],
    },
  };
  const read = makeResidentGatewayStatusReader({
    agentId: "bob",
    gatewayUrl: "ws://127.0.0.1:9100/api/ws?token=x",
    openWs: async () => fakeWsClient(resp),
    readSessionId: () => "", // not bound yet
  });
  assert.equal(await read(), "working", "most-recent row status is used when id+key miss");
});

test("makeResidentGatewayStatusReader: a WS open / RPC error reads as '' (never a false turn-end)", async () => {
  const read = makeResidentGatewayStatusReader({
    agentId: "carol",
    gatewayUrl: "ws://127.0.0.1:9100/api/ws?token=x",
    openWs: async () => { throw new Error("gateway down"); },
    readSessionId: () => "real-1",
  });
  assert.equal(await read(), "", "an unreadable gateway is '' (transient no-op for the detector)");
});

test("makeResidentGatewayStatusReader: backs off connecting a sustained-dead gateway, recovers on success", async () => {
  let opens = 0;
  let alive = false;
  const read = makeResidentGatewayStatusReader({
    agentId: "dave",
    gatewayUrl: "ws://127.0.0.1:9100/api/ws?token=x",
    openWs: async () => {
      opens += 1;
      if (!alive) throw new Error("gateway down");
      return { request: async () => ({ sessions: [{ id: "real-1", status: "working" }] }), close() {} };
    },
    readSessionId: () => "real-1",
  });
  // 30 reads against a DEAD gateway: without backoff that's 30 connect attempts; with it,
  // ~3 (threshold) + 1-in-10 thereafter — far fewer. All reads still return "" (detector no-op).
  for (let i = 0; i < 30; i++) assert.equal(await read(), "");
  assert.ok(opens <= 8, `backed off a dead gateway (got ${opens} connect attempts, expected <= 8)`);
  // Gateway recovers → the next allowed probe succeeds and resets the backoff.
  alive = true;
  let recovered = "";
  for (let i = 0; i < 12 && recovered !== "working"; i++) recovered = await read();
  assert.equal(recovered, "working", "a recovered gateway resumes reads after the backoff window");
});

// ---------------------------------------------------------------------------
// End-to-end wiring: the resident reader feeds the SAME
// startHermesGatewayTurnDetector the managed loop uses. A stubbed gateway that
// reads "working" then sustained "idle" must POST exactly one turn-start then one
// turn-end (anti-feedback: never fabricates working).
// ---------------------------------------------------------------------------

test("resident wiring: working → sustained idle posts exactly one turn-start then one turn-end", async () => {
  let starts = 0;
  let ends = 0;
  const statuses = ["working", "idle", "idle", "idle", "idle"];
  let i = 0;
  // One persistent fake client whose active_list status ADVANCES per request, so
  // the (reused) reader observes working → sustained idle across ticks.
  const client = fakeWsClient(() => ({
    result: { sessions: [{ session_id: "real-d", title: "t", status: statuses[Math.min(i++, statuses.length - 1)], started_at: "2026-06-07T10:00:01Z" }] },
  }));
  const read = makeResidentGatewayStatusReader({
    agentId: "dave",
    gatewayUrl: "ws://127.0.0.1:9100/api/ws?token=x",
    openWs: async () => client,
    readSessionId: () => "real-d",
  });
  const stop = startHermesGatewayTurnDetector({
    intervalMs: 5,
    idleDebounce: 2,
    workingRefreshMs: 0, // edge-only for a deterministic count
    readGatewayStatus: read,
    postTurnStart: async () => { starts++; },
    postTurnEnd: async () => { ends++; },
  });
  await new Promise((r) => setTimeout(r, 80));
  stop();
  assert.equal(starts, 1, "exactly one turn-start on the gateway 'working' edge");
  assert.equal(ends, 1, "exactly one turn-end on sustained gateway idle");
});

console.log("resident-hermes-turn-detector.test.js: all assertions passed");
