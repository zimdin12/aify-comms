#!/usr/bin/env node
// Lock the post-restart resolve path: after an aify-comms restart the gateway
// session.active_list is EMPTY (no live sessions yet) but the SessionDB (session.list)
// still holds the agent's durable row. A DURABLE marker must resolve from the DB and
// must NOT be cleared — clearing a still-resumable marker is the very "lost history on
// restart" bug. Guards against a Plan-3 (durable-marker) regression.
import assert from "node:assert/strict";
import { runResolveSessionCli } from "../hermes-active-session.mjs";

const DURABLE = "20260605_054210_abc123";

// active_list empty (post-restart); session.list (DB) has the durable row.
const emptyActiveList = { result: { sessions: [] } };
const dbList = {
  result: {
    sessions: [{ id: "deadsid", session_key: DURABLE, last_active: "2026-06-05T05:42:10.000Z" }],
  },
};

function clientReturning(frames) {
  let i = 0;
  return { request: async () => frames[i++], close() {} };
}

const writes = [];
let cleared = false;
const res = await runResolveSessionCli("next-senior-dev", {
  gatewayUrl: "ws://test",
  openClient: async () => clientReturning([emptyActiveList, dbList]),
  readMarker: () => DURABLE,
  writeMarker: (id, v) => writes.push(v),
  clearMarker: () => { cleared = true; },
  writeActiveSessionFile: () => {},
  out: () => {},
  err: () => {},
  tempDir: "/tmp",
});

assert.equal(res.resolved, DURABLE, "durable marker resolves from SessionDB across an empty post-restart active_list");
assert.equal(cleared, false, "a still-resumable marker must never be cleared");

console.log("hermes-resume-survives-restart.test.js: all assertions passed");
