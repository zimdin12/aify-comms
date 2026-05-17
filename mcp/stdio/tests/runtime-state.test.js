#!/usr/bin/env node
import assert from "node:assert/strict";
import { runtimeStateWithoutSessionHandle } from "../runtimes.js";

assert.deepEqual(
  runtimeStateWithoutSessionHandle("pi", {
    sessionId: "pi-session",
    sessionFile: "/tmp/pi.jsonl",
    bridgeInstanceId: "bridge-1",
    environmentId: "env-1",
  }),
  {
    bridgeInstanceId: "bridge-1",
    environmentId: "env-1",
  },
  "Pi runtime-state clear should remove stale sessionId/sessionFile but preserve bridge/environment metadata",
);

assert.deepEqual(
  runtimeStateWithoutSessionHandle("codex", {
    threadId: "thread-1",
    bridgeInstanceId: "bridge-1",
  }),
  {
    bridgeInstanceId: "bridge-1",
  },
  "Codex runtime-state clear should remove stale threadId",
);

assert.deepEqual(
  runtimeStateWithoutSessionHandle("hermes", {
    sessionId: "hermes-session",
    bridgeInstanceId: "bridge-1",
  }),
  {
    bridgeInstanceId: "bridge-1",
  },
  "Hermes runtime-state clear should remove stale sessionId",
);

console.log("runtime-state.test.js: all assertions passed");
