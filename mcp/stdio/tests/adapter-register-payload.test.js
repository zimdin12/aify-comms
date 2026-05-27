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

test("fillSessionHandleFromAdapter ignores Hermes env handle for fresh live gateway", () => {
  process.env.HERMES_SESSION_ID = "historical-visible-looking-session";
  process.env.AIFY_HERMES_GATEWAY_URL = "ws://127.0.0.1:9999/api/ws?token=x";
  delete process.env.AIFY_EXPLICIT_SESSION_HANDLE;
  const adapter = adapterFor("hermes");
  const args = { agentId: "h" };
  const out = fillSessionHandleFromAdapter(args, adapter);
  assert.strictEqual(out.sessionHandle || "", "");
  delete process.env.HERMES_SESSION_ID;
  delete process.env.AIFY_HERMES_GATEWAY_URL;
});

test("fillSessionHandleFromAdapter keeps explicit Hermes resume handle", () => {
  process.env.HERMES_SESSION_ID = "explicit-resume-session";
  process.env.AIFY_HERMES_GATEWAY_URL = "ws://127.0.0.1:9999/api/ws?token=x";
  process.env.AIFY_EXPLICIT_SESSION_HANDLE = "true";
  const adapter = adapterFor("hermes");
  const args = { agentId: "h" };
  const out = fillSessionHandleFromAdapter(args, adapter);
  assert.strictEqual(out.sessionHandle, "explicit-resume-session");
  delete process.env.HERMES_SESSION_ID;
  delete process.env.AIFY_HERMES_GATEWAY_URL;
  delete process.env.AIFY_EXPLICIT_SESSION_HANDLE;
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
