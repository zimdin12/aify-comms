#!/usr/bin/env node
import assert from "node:assert/strict";
import { shutdownAllCodexSessions, __injectCodexSessionForTests, __codexSessionPoolSize } from "../codex-session.js";

const stopped = [];
__injectCodexSessionForTests("agent-a", { stop: async () => { stopped.push("agent-a"); } });
__injectCodexSessionForTests("agent-b", { stop: async () => { stopped.push("agent-b"); } });
assert.equal(__codexSessionPoolSize(), 2);

await shutdownAllCodexSessions("test");
assert.deepEqual(stopped.sort(), ["agent-a", "agent-b"]);
assert.equal(__codexSessionPoolSize(), 0, "pool cleared after shutdownAll");
console.log("codex-shutdown-all.test.js: all assertions passed");
