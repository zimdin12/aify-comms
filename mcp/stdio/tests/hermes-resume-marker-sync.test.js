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

console.log("hermes-resume-marker-sync.test.js: all assertions passed");
