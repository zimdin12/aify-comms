#!/usr/bin/env node
import assert from "node:assert/strict";

const { isFatalCodexRuntimeLog } = await import("../runtimes.js");

assert.equal(
  isFatalCodexRuntimeLog('ERROR rmcp::transport::worker: worker quit with fatal: Transport channel closed, when Client(HttpRequest(HttpRequest("http/request failed")))'),
  true,
);
// 2026-06-03: a transient websocket close (1006/1000/1001) is NO LONGER classified
// fatal — it was tearing down a healthy managed-codex session + failing the turn
// ("conversation interrupted") on a brief disconnect. Only "worker quit with fatal"
// / "Transport channel closed" stay fatal; a bare WS close is handled as
// reconnectable by the controller, not an instant kill.
assert.equal(isFatalCodexRuntimeLog("Codex WebSocket app-server connection closed (1006)"), false);
assert.equal(isFatalCodexRuntimeLog("Codex could not find bubblewrap on PATH. Codex will use the vendored bubblewrap in the meantime."), false);
assert.equal(isFatalCodexRuntimeLog("regular model progress log"), false);

console.log("codex-runtime-fatal-log.test.js: all assertions passed");
