#!/usr/bin/env node
// Nested-bridge guard: when a runtime adapter launches an RPC child (e.g.
// `omp --mode rpc --resume <session>`), the parent sets
// AIFY_BRIDGE_DISABLED=1 in the child env. The child's inherited
// mcp/stdio/server.js must exit cleanly at startup instead of registering
// as the same agent and superseding the resident bridge.
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { sealedChildEnv } from "./_child-env.mjs";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.join(__dirname, "..", "server.js");

function runServer(env) {
  return new Promise((resolve, reject) => {
    const proc = spawn(process.execPath, [SERVER], {
      env: { ...sealedChildEnv(), ...env },
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    proc.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    const timeout = setTimeout(() => {
      try { proc.kill("SIGKILL"); } catch {}
      reject(new Error(`server.js did not exit within 15s. stdout=${stdout} stderr=${stderr}`));
    }, 15000);
    proc.once("exit", (code, signal) => {
      clearTimeout(timeout);
      resolve({ code, signal, stdout, stderr });
    });
    proc.once("error", (err) => {
      clearTimeout(timeout);
      reject(err);
    });
  });
}

const result = await runServer({
  AIFY_BRIDGE_DISABLED: "1",
  AIFY_HERMES_ACTIVE_SESSION_FILE: "",
  HERMES_TUI_ACTIVE_SESSION_FILE: "",
  HERMES_SESSION_ID: "",
  HERMES_SESSION: "",
  // Sanity: even with an agent id present (the RPC child INHERITS its
  // parent's env), the disabled flag must short-circuit before any
  // registration / claim work.
  AIFY_AGENT_ID: "should-not-register",
});

assert.equal(result.code, 0, `server.js with AIFY_BRIDGE_DISABLED=1 should exit cleanly. stderr=${result.stderr}`);
assert.ok(!result.stdout.includes("registered"), "no registration messages expected from a disabled-bridge server.js");

// runtimeChildEnv must NOT default AIFY_BRIDGE_DISABLED. Wrapper children
// (claude-aify → claude → mcp/stdio/server.js, codex/hermes/opencode
// equivalents) legitimately host MCP servers that need the aify env.
// Defaulting the disabled flag was a real regression — it broke
// claude-code permissions/MCP integration. Only known RPC-child spawn
// sites (pi `omp --mode rpc`) set the flag explicitly via extraEnv.
const { runtimeChildEnv } = await import("../runtimes.js");
const defaultEnv = runtimeChildEnv({ EXTRA_VAR: "value" });
assert.notEqual(defaultEnv.AIFY_BRIDGE_DISABLED, "1", "runtimeChildEnv must NOT default AIFY_BRIDGE_DISABLED — it would break wrapper MCP chains");
assert.equal(defaultEnv.EXTRA_VAR, "value", "explicit extras must still flow through");

// Explicit per-call extraEnv DOES set the flag for the specific spawn
// site that needs it (the pi RPC child).
const explicitEnv = runtimeChildEnv({ AIFY_BRIDGE_DISABLED: "1", AIFY_AGENT_ID: "" });
assert.equal(explicitEnv.AIFY_BRIDGE_DISABLED, "1", "explicit per-call AIFY_BRIDGE_DISABLED must propagate");
assert.equal(explicitEnv.AIFY_AGENT_ID, "", "explicit AIFY_AGENT_ID clearing must propagate");

console.log("OK rpc-child-bridge-disabled: server.js exits cleanly with AIFY_BRIDGE_DISABLED=1; runtimeChildEnv DOES NOT default the flag (wrapper MCP chains preserved)");
