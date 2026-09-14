// Closing a resident's terminal reads offline at once, not after the 150s lease.
//
// A terminal window closing sends SIGHUP, and a client that dies closes the bridge's stdin. Neither
// reached `shutdownWithStatus`: Node's default SIGHUP action kills the process without running any
// handler, and the MCP SDK's stdio transport does not listen for stdin ending, so the bridge lingered
// until the harness-pid guard noticed. Either way no `resident-lost` was posted and the agent kept
// reading `available` for the whole heartbeat lease.
//
// This runs the real bridge as an entrypoint against a stub service. The stub listens on 127.0.0.2
// because a 127.0.0.1 primary makes the endpoint module add the operator's real 127.0.0.1:8800 as a
// retriable fallback, and the bridge's boot requests are retriable.

import assert from "node:assert/strict";
import { test } from "node:test";
import { createServer } from "node:http";
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { sealedChildEnv } from "./_child-env.mjs";

const SERVER = join(dirname(fileURLToPath(import.meta.url)), "..", "server.js");

async function runBridge({ sessionMode, end }) {
  const paths = [];
  const service = createServer((req, res) => {
    paths.push(`${req.method} ${req.url.split("?")[0]}`);
    req.resume();
    req.on("end", () => { res.setHeader("content-type", "application/json"); res.end("{}"); });
  });
  await new Promise((r) => service.listen(0, "127.0.0.2", r));
  const home = mkdtempSync(join(tmpdir(), "aify-bridge-hup-"));
  const child = spawn(process.execPath, [SERVER], {
    cwd: home,
    env: sealedChildEnv({
      HOME: home,
      USERPROFILE: home,
      XDG_STATE_HOME: home,
      AIFY_SERVER_URL: `http://127.0.0.2:${service.address().port}`,
      AIFY_AGENT_ID: "hup-agent",
      AIFY_SESSION_MODE: sessionMode,
      AIFY_RUNTIME: "claude-code",
    }),
    stdio: ["pipe", "pipe", "pipe"],
  });
  child.stdout.resume();
  let stderr = "";
  const exited = new Promise((resolve) => child.on("exit", (code, signal) => resolve({ code, signal })));
  try {
    await new Promise((resolve, reject) => {
      const t = setTimeout(() => reject(new Error(`bridge never came up: ${stderr}`)), 15000);
      child.stderr.on("data", (c) => {
        stderr += c;
        if (stderr.includes("running on stdio")) { clearTimeout(t); resolve(); }
      });
    });
    const sent = Date.now();
    end(child);
    const result = await Promise.race([exited, new Promise((r) => setTimeout(() => r(null), 8000))]);
    return { result, ms: Date.now() - sent, paths };
  } finally {
    child.kill("SIGKILL");
    service.closeAllConnections?.();
    service.close();
    rmSync(home, { recursive: true, force: true });
  }
}

const lost = (paths) => paths.filter((p) => p === "POST /api/v1/agents/hup-agent/resident-lost");

test("a resident bridge whose stdin ends reports resident-lost and exits", async () => {
  const { result, paths } = await runBridge({ sessionMode: "resident", end: (c) => c.stdin.end() });
  assert.ok(result, "the bridge must exit when its client closes stdin");
  assert.equal(lost(paths).length, 1, paths.join("\n"));
});

test("a resident bridge given SIGHUP reports resident-lost before exiting", { skip: process.platform === "win32" }, async () => {
  const { result, paths } = await runBridge({ sessionMode: "resident", end: (c) => c.kill("SIGHUP") });
  assert.ok(result, "the bridge must exit on SIGHUP");
  assert.equal(lost(paths).length, 1, paths.join("\n"));
});

test("a managed bridge exits on the same events and never reports resident-lost", async () => {
  for (const end of [(c) => c.stdin.end(), ...(process.platform === "win32" ? [] : [(c) => c.kill("SIGHUP")])]) {
    const { result, paths } = await runBridge({ sessionMode: "managed", end });
    assert.ok(result, "a managed bridge must exit too");
    assert.equal(lost(paths).length, 0, paths.join("\n"));
  }
});
