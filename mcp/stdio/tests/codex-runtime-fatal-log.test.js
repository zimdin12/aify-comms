#!/usr/bin/env node
import assert from "node:assert/strict";

const { isFatalCodexRuntimeLog } = await import("../runtimes.js");

assert.equal(
  isFatalCodexRuntimeLog('ERROR rmcp::transport::worker: worker quit with fatal: Transport channel closed, when Client(HttpRequest(HttpRequest("http/request failed")))'),
  true,
);
assert.equal(isFatalCodexRuntimeLog("Codex WebSocket app-server connection closed (1006)"), true);
assert.equal(isFatalCodexRuntimeLog("Codex could not find bubblewrap on PATH. Codex will use the vendored bubblewrap in the meantime."), false);
assert.equal(isFatalCodexRuntimeLog("regular model progress log"), false);

console.log("codex-runtime-fatal-log.test.js: all assertions passed");
