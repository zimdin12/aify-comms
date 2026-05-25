import assert from "assert";
import test from "node:test";

import { adapterFor } from "../adapters/index.js";

// Helper extracted from server.js's comms_register handler. Once Plan 1 lands,
// this helper lives at mcp/stdio/register-helpers.js and is exported for
// testing via fillSessionHandleFromAdapter.
import { fillSessionHandleFromAdapter } from "../register-helpers.js";

test("fillSessionHandleFromAdapter preserves caller-supplied handle", () => {
  process.env.CLAUDE_SESSION_ID = "from-env";
  const adapter = adapterFor("claude-code");
  const args = { agentId: "a", sessionHandle: "caller-handle" };
  const out = fillSessionHandleFromAdapter(args, adapter);
  assert.strictEqual(out.sessionHandle, "caller-handle");
  delete process.env.CLAUDE_SESSION_ID;
});

test("fillSessionHandleFromAdapter fills empty sessionHandle from adapter env", () => {
  process.env.CLAUDE_SESSION_ID = "from-env";
  const adapter = adapterFor("claude-code");
  const args = { agentId: "a" };
  const out = fillSessionHandleFromAdapter(args, adapter);
  assert.strictEqual(out.sessionHandle, "from-env");
  delete process.env.CLAUDE_SESSION_ID;
});

test("fillSessionHandleFromAdapter leaves empty when env has no handle", () => {
  delete process.env.CLAUDE_SESSION_ID;
  const adapter = adapterFor("claude-code");
  const args = { agentId: "a" };
  const out = fillSessionHandleFromAdapter(args, adapter);
  assert.strictEqual(out.sessionHandle || "", "");
});

test("fillSessionHandleFromAdapter is a no-op with null adapter", () => {
  const args = { agentId: "a" };
  const out = fillSessionHandleFromAdapter(args, null);
  assert.deepStrictEqual(out, args);
});
