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

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SERVER = path.join(__dirname, "..", "server.js");

function runServer(env) {
  return new Promise((resolve, reject) => {
    const proc = spawn(process.execPath, [SERVER], {
      env: { ...process.env, ...env },
      stdio: ["pipe", "pipe", "pipe"],
      windowsHide: true,
    });
    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (chunk) => { stdout += chunk.toString(); });
    proc.stderr.on("data", (chunk) => { stderr += chunk.toString(); });
    const timeout = setTimeout(() => {
      try { proc.kill("SIGKILL"); } catch {}
      reject(new Error(`server.js did not exit within 5s. stdout=${stdout} stderr=${stderr}`));
    }, 5000);
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
  // Sanity: even with an agent id present (the RPC child INHERITS its
  // parent's env), the disabled flag must short-circuit before any
  // registration / claim work.
  AIFY_AGENT_ID: "should-not-register",
});

assert.equal(result.code, 0, `server.js with AIFY_BRIDGE_DISABLED=1 should exit cleanly. stderr=${result.stderr}`);
assert.ok(!result.stdout.includes("registered"), "no registration messages expected from a disabled-bridge server.js");

// Belt-and-braces: runtimeChildEnv defaults the disabled flag and clears
// AIFY_AGENT_ID for every runtime child, so a future runtime adapter that
// forgets to set explicit env still gets the protection.
const { runtimeChildEnv } = await import("../runtimes.js");
const childEnv = runtimeChildEnv({ EXTRA_VAR: "value" });
assert.equal(childEnv.AIFY_BRIDGE_DISABLED, "1", "AIFY_BRIDGE_DISABLED must default to '1' for runtime children");
assert.equal(childEnv.AIFY_AGENT_ID, "", "AIFY_AGENT_ID must be cleared for runtime children");
assert.equal(childEnv.EXTRA_VAR, "value", "explicit extras must still flow through");

// Caller can OVERRIDE the disabled flag if they really need to (e.g. for an
// agent-runtime child that legitimately registers). The default is the safe
// state; explicit opt-in stays possible.
const overrideEnv = runtimeChildEnv({ AIFY_BRIDGE_DISABLED: "" });
assert.equal(overrideEnv.AIFY_BRIDGE_DISABLED, "", "explicit AIFY_BRIDGE_DISABLED override must win over the default");

console.log("OK rpc-child-bridge-disabled: server.js exits cleanly with AIFY_BRIDGE_DISABLED=1; runtimeChildEnv defaults verified");
