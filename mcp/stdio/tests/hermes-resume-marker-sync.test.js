#!/usr/bin/env node
// startResumeMarkerSync keeps the durable resume marker + aify handle tracking the TUI's live
// session, independent of aify-comms delivery — fixing "fresh (untitled) session on every restart".
import assert from "node:assert/strict";
import { startResumeMarkerSync } from "../hermes-active-session.mjs";

const tick = (ms = 40) => new Promise((r) => setTimeout(r, ms));

// A gateway active_list with one live session whose durable id is a session_key-style key.
const liveList = {
  sessions: [
    { id: "20260605_161037_c57d9a", title: "Context Retention Banana Test", started_at: "2026-06-05T16:10:37.000Z", message_count: 6 },
  ],
};

function harness({ list, marker, gatewayUrl = "ws://127.0.0.1:8926/api/ws?token=t" } = {}) {
  const writes = [];
  const patches = [];
  const stop = startResumeMarkerSync({
    agentId: "next-tech-lead",
    intervalMs: 1_000_000, // don't let the interval re-fire; we assert the immediate tick
    tempDir: "/tmp",
    openWs: async () => ({ request: async () => list, close() {} }),
    readGatewayUrl: () => (gatewayUrl ? { gatewayUrl } : null),
    readMarker: () => marker,
    writeMarker: (id, v) => writes.push([id, v]),
    httpCall: async (m, path, body) => { patches.push([m, path, body]); },
  });
  return { stop, writes, patches };
}

// (1) Stale marker + a live session → marker updated to the live session's DURABLE key + PATCH fired.
{
  const { stop, writes, patches } = harness({ list: liveList, marker: "20260605_054328_DEADKEY" });
  await tick();
  stop();
  assert.deepEqual(writes, [["next-tech-lead", "20260605_161037_c57d9a"]], "marker set to the live durable key");
  assert.equal(patches.length, 1, "aify handle PATCHed once");
  assert.equal(patches[0][1], "/agents/next-tech-lead/session-handle");
  assert.equal(patches[0][2].sessionHandle, "20260605_161037_c57d9a");
}

// (2) Marker already correct → no write, no PATCH (idempotent).
{
  const { stop, writes, patches } = harness({ list: liveList, marker: "20260605_161037_c57d9a" });
  await tick();
  stop();
  assert.equal(writes.length, 0, "no redundant write when marker already matches");
  assert.equal(patches.length, 0);
}

// (3) Empty active_list (gateway idle/restarting) → leave the marker UNCHANGED (never clears here).
{
  const { stop, writes, patches } = harness({ list: { sessions: [] }, marker: "20260605_054328_DEADKEY" });
  await tick();
  stop();
  assert.equal(writes.length, 0, "empty active_list must not touch the marker");
  assert.equal(patches.length, 0);
}

// (4) No gateway URL yet → no-op.
{
  const { stop, writes } = harness({ list: liveList, marker: "x", gatewayUrl: "" });
  await tick();
  stop();
  assert.equal(writes.length, 0, "no gateway URL → no-op");
}

// ── the DISABLED sync still hands back a stop ────────────────────────────────
//
// Twenty-third cluster off the V8-coverage census: the early-return `() => {}` — the stop function a disabled
// sync returns. The four cases above all call `stop()`, but on a sync that really started; nothing had ever
// called the no-op. It matters because teardown is UNCONDITIONAL: `server.js` keeps whatever this returned and
// calls it on shutdown. Returning undefined for a disabled sync would make shutdown throw, which on the
// graceful path is how a teardown stops halfway through.

// (5) A blank agent id, or an interval that is not a positive number, disables the sync — and the stop is still
//     callable, more than once, without touching a gateway.
{
  for (const opts of [
    { agentId: "", intervalMs: 1000 },
    { agentId: "   ", intervalMs: 1000 },
    { agentId: "a", intervalMs: 0 },
    { agentId: "a", intervalMs: -5 },
    { agentId: "a", intervalMs: Number.NaN },
    { agentId: "a", intervalMs: Number.POSITIVE_INFINITY },
    // NOT in this list: `{ agentId: "a" }` with no interval. `intervalMs` defaults to 20_000, so omitting it
    // starts the sync — asserted separately below, because I first assumed the opposite.
  ]) {
    let opened = 0;
    const stop = startResumeMarkerSync({
      ...opts,
      tempDir: "/tmp",
      openWs: async () => { opened += 1; return { request: async () => liveList, close() {} }; },
      readGatewayUrl: () => ({ gatewayUrl: "ws://127.0.0.1:8926/api/ws?token=t" }),
      readMarker: () => "",
      writeMarker: () => {},
      httpCall: async () => {},
    });
    assert.equal(typeof stop, "function", `${JSON.stringify(opts)}: no stop function was returned`);
    await tick();
    assert.equal(opened, 0, `${JSON.stringify(opts)}: a disabled sync still talked to the gateway`);
    stop();
    stop(); // teardown paths can call it twice; it must stay harmless
  }
}

// (5b) An OMITTED interval is the live default (20s), not "disabled": the sync starts and its prompt first tick
//      runs. That default is what a caller who wants the sync at all relies on getting.
{
  let opened = 0;
  const stop = startResumeMarkerSync({
    agentId: "next-tech-lead",
    tempDir: "/tmp",
    openWs: async () => { opened += 1; return { request: async () => liveList, close() {} }; },
    readGatewayUrl: () => ({ gatewayUrl: "ws://127.0.0.1:8926/api/ws?token=t" }),
    readMarker: () => "20260605_161037_c57d9a",
    writeMarker: () => {},
    httpCall: async () => {},
  });
  await tick();
  stop();
  assert.equal(opened, 1, "an omitted interval must still start the sync and fire its first tick once");
}

// (6) stop() ends the SCHEDULE, not just the current tick: a stopped sync performs no further work even though
//     its interval was short enough to fire several more times.
//
//     TWO MECHANISMS, EACH SUFFICIENT HERE — `clearInterval(timer)` and the `stopped` flag the tick checks on
//     entry. Removing either alone survives this case (the other still holds); removing both, so the stop does
//     nothing at all, IS caught. They are not redundant in production: `clearInterval` cannot stop a tick that
//     is ALREADY mid-await, and that await chain reaches the gateway and then PATCHes the service. The flag is
//     what closes that window — the same shape as the teardown window where a bridge reported OFFLINE and kept
//     claiming for the length of its await chain. A fake that resolves instantly cannot open the window, which
//     is why the mutations for it survive rather than being wrong.
{
  let opened = 0;
  const stop = startResumeMarkerSync({
    agentId: "next-tech-lead",
    intervalMs: 20,
    tempDir: "/tmp",
    openWs: async () => { opened += 1; return { request: async () => liveList, close() {} }; },
    readGatewayUrl: () => ({ gatewayUrl: "ws://127.0.0.1:8926/api/ws?token=t" }),
    readMarker: () => "20260605_161037_c57d9a",
    writeMarker: () => {},
    httpCall: async () => {},
  });
  await tick(120);
  const beforeStop = opened;
  assert.ok(beforeStop >= 2, `the interval never re-fired (${beforeStop} reads) — this case proves nothing`);
  stop();
  await tick(160);
  assert.equal(opened, beforeStop, `the sync kept polling after stop() (${opened} vs ${beforeStop})`);
}

console.log("hermes-resume-marker-sync.test.js: all assertions passed");
