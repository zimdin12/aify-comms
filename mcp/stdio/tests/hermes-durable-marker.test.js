#!/usr/bin/env node
// Repro + lock for the "fresh session after aify-comms restart" bug: the delivery
// loop's fallback (waitForActiveSession) must persist the DURABLE session_key to the
// resume marker, never the ephemeral runtime id. The ephemeral id is still returned
// for THIS delivery (prompt.submit/steer need the live id).
import assert from "node:assert/strict";
import { waitForActiveSession } from "../hermes-managed-host.js";

// An active_list row whose ephemeral runtime id differs from its durable session_key.
const EPHEMERAL = "5af7c19c";
const DURABLE = "20260605_054210_abc123";
const activeListResponse = {
  result: {
    sessions: [
      {
        id: EPHEMERAL,
        session_key: DURABLE,
        last_active: "2026-06-05T05:42:10.000Z",
        attached: true,
      },
    ],
  },
};

const markerWrites = [];
const wsClient = { request: async () => activeListResponse };

let idc = 0;
const sessionId = await waitForActiveSession({
  wsClient,
  agentId: "next-senior-dev",
  wantId: "", // no bound id → most-recent fallback
  nextId: () => ++idc,
  tempDir: "/tmp",
  readMarker: () => "", // marker empty so fallback runs
  writeMarker: (id, value) => markerWrites.push([id, value]),
  now: () => 10_000,
  since: 0, // freshnessFloor = 0
  graceMs: 0, // grace already elapsed → fallback binds immediately
  deadlineMs: 1_000,
  intervalMs: 1,
  sleepImpl: async () => {},
  log: () => {},
});

// Delivery uses the ephemeral live id (correct — prompt.submit needs it).
assert.equal(sessionId, EPHEMERAL, "delivery binds the ephemeral live id");

// THE FIX: the marker must receive the DURABLE session_key, not the ephemeral id.
const lastMarker = markerWrites.filter((w) => w[0] === "next-senior-dev").pop();
assert.ok(lastMarker, "a marker was written for the fallback session");
assert.equal(
  lastMarker[1],
  DURABLE,
  `marker must hold the DURABLE session_key, got '${lastMarker[1]}'`,
);

console.log("hermes-durable-marker.test.js: all assertions passed");
