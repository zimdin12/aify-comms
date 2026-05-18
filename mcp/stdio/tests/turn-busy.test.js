#!/usr/bin/env node
import assert from "node:assert/strict";
import { agentHeartbeatPayload } from "../turn-busy.js";

assert.deepEqual(
  agentHeartbeatPayload({ bridgeId: "bridge-1", machineId: "machine-1" }),
  { bridgeId: "bridge-1", machineId: "machine-1" },
  "idle heartbeat must not mutate turn-busy state",
);

assert.deepEqual(
  agentHeartbeatPayload({
    bridgeId: "bridge-1",
    machineId: "machine-1",
    turnBusy: true,
    turnRunId: "run-1",
    turnRuntime: "codex",
  }),
  {
    bridgeId: "bridge-1",
    machineId: "machine-1",
    turnBusy: true,
    turnRunId: "run-1",
    turnRuntime: "codex",
  },
  "busy heartbeat must include explicit run and runtime ownership",
);

assert.deepEqual(
  agentHeartbeatPayload({
    bridgeId: "bridge-1",
    machineId: "machine-1",
    turnBusy: false,
    turnRunId: "run-1",
    turnRuntime: "codex",
  }),
  {
    bridgeId: "bridge-1",
    machineId: "machine-1",
    turnBusy: false,
    turnRunId: "run-1",
    turnRuntime: "codex",
  },
  "clear heartbeat must be explicit and tied to the run it clears",
);

console.log("turn-busy.test.js: all assertions passed");
