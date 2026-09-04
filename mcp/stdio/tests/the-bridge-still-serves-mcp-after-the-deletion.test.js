#!/usr/bin/env node
// server.js, LAUNCHED THE WAY A WRAPPER LAUNCHES IT, still answers MCP.
//
// WHY THIS FILE EXISTS, and it is the gap the whole v0.6.2 deletion sat over. 390 suites are green,
// and not one of them ever RAN this module as an entrypoint speaking its protocol. The closest,
// `server-import-does-not-boot-a-bridge.test.js`, imports it as a NON-entrypoint and proves it starts
// nothing — the opposite property. Everything else tests an extracted module by calling it directly.
//
// So the honest claim after deleting sixteen modules and 289 lines out of this file was "passes in
// tests", never "works". The distinction is not academic here: a resident bridge's ENTIRE JOB is to
// come up over stdio, answer `initialize`, and list its tools. A deletion that broke an import at
// module scope, or left `registerAllTools` reaching for something that went with the cluster, would
// show as green everywhere and fail on the first agent to launch — after `install.sh` had already
// copied it over the working one, on a host with a live team on it.
//
// WHAT IT PROVES: the process starts, completes the MCP handshake, lists its tools, and exits. That
// is the wrapper's contract with this file.
//
// THE ENVIRONMENT IS SEALED HOSTILE — `sealedChildEnv()` plus endpoints pointing at 127.0.0.2:1.
// This launches server.js as an ENTRYPOINT, so `main()` runs, and main() is the half that registers
// and heartbeats. An unset URL falls back to `defaultFallbackServerUrls`, which adds the operator's
// real 127.0.0.1:8800 — the path by which an earlier test in this repo registered six agents into
// the production registry. `AIFY_AGENT_ID` is deleted by the seal, so there is no identity to
// register even if it could reach a service.

import assert from "node:assert/strict";
import test from "node:test";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { sealedChildEnv } from "./_child-env.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const SERVER = path.join(STDIO, "server.js");

/** One MCP request, framed as the stdio transport frames it: one JSON object per line. */
function frame(message) {
  return JSON.stringify(message) + "\n";
}

/**
 * Launch server.js over stdio, speak MCP to it, and report what came back.
 *
 * Reads LINE BY LINE rather than waiting for exit, because the server is a long-lived process: a test
 * that awaited its exit would hang for the full timeout and then report a timeout instead of an
 * answer. It is killed as soon as both replies have arrived.
 */
function handshake({ timeoutMs = 30_000 } = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [SERVER], {
      cwd: STDIO,
      env: sealedChildEnv({
        // SET, and pointing nowhere. See the header.
        AIFY_SERVER_URL: "http://127.0.0.2:1",
        CLAUDE_MCP_SERVER_URL: "http://127.0.0.2:1",
        AIFY_SERVER_FALLBACK_URLS: "http://127.0.0.2:1",
        CLAUDE_MCP_FALLBACK_URLS: "http://127.0.0.2:1",
      }),
      stdio: ["pipe", "pipe", "pipe"],
    });

    const replies = new Map();
    const stderr = [];
    let buffered = "";
    let settled = false;

    const finish = (fn, arg) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try { child.kill(); } catch { /* already gone */ }
      fn(arg);
    };

    const timer = setTimeout(
      () => finish(reject, new Error(
        `server.js did not complete the MCP handshake within ${timeoutMs}ms. `
        + `Replies seen: ${[...replies.keys()].join(", ") || "none"}. `
        + `stderr: ${stderr.join("").slice(-2000)}`,
      )),
      timeoutMs,
    );

    child.on("error", (error) => finish(reject, error));
    child.stderr.on("data", (chunk) => stderr.push(String(chunk)));
    child.on("exit", (code) => {
      if (settled) return;
      finish(reject, new Error(
        `server.js exited (code ${code}) before answering. This is the shape a broken import at `
        + `module scope takes. stderr: ${stderr.join("").slice(-2000)}`,
      ));
    });

    child.stdout.on("data", (chunk) => {
      buffered += String(chunk);
      let cut;
      while ((cut = buffered.indexOf("\n")) >= 0) {
        const line = buffered.slice(0, cut).trim();
        buffered = buffered.slice(cut + 1);
        if (!line) continue;
        let message;
        try { message = JSON.parse(line); } catch { continue; }
        if (message.id !== undefined) replies.set(message.id, message);
        if (replies.has(1) && replies.has(2)) {
          finish(resolve, { replies, stderr: stderr.join("") });
        }
      }
    });

    child.stdin.write(frame({
      jsonrpc: "2.0",
      id: 1,
      method: "initialize",
      params: {
        protocolVersion: "2024-11-05",
        capabilities: {},
        clientInfo: { name: "aify-comms-deletion-proof", version: "0" },
      },
    }));
    child.stdin.write(frame({ jsonrpc: "2.0", method: "notifications/initialized" }));
    child.stdin.write(frame({ jsonrpc: "2.0", id: 2, method: "tools/list", params: {} }));
  });
}

let RESULT = null;
/** One launch, read by every test below — a process start per assertion is a slow suite for no gain. */
async function once() {
  if (!RESULT) RESULT = await handshake();
  return RESULT;
}

test("it completes the MCP handshake", async () => {
  const { replies } = await once();
  const initialize = replies.get(1);
  assert.ok(initialize, "server.js never answered `initialize`");
  assert.equal(
    initialize.error, undefined,
    `initialize returned an error: ${JSON.stringify(initialize.error)}`,
  );
  assert.equal(
    initialize.result?.serverInfo?.name, "aify-comms-mcp",
    `initialize answered, but as ${JSON.stringify(initialize.result?.serverInfo)}`,
  );
});

test("it lists its tools, which is the whole of what a wrapper asks it for", async () => {
  const { replies } = await once();
  const list = replies.get(2);
  assert.ok(list, "server.js never answered `tools/list`");
  assert.equal(list.error, undefined, `tools/list returned an error: ${JSON.stringify(list.error)}`);
  const tools = list.result?.tools || [];

  // A FLOOR, NOT A COUNT. The exact number is `tool-surface-ratchet`'s job and it moves with every
  // tool added; what this file must catch is a registration path that broke and left a handful.
  assert.ok(
    tools.length >= 30,
    `only ${tools.length} tool(s) registered. registerAllTools reaches many modules, and one of them `
    + "failing to contribute is invisible to every unit test of those modules.",
  );

  // The named ones are the load-bearing surface: an agent that cannot send, read or register is a
  // bridge that started and does nothing. Named rather than counted so a rename is a decision.
  for (const name of ["comms_send", "comms_inbox", "comms_register", "comms_agents"]) {
    assert.ok(
      tools.some((t) => t.name === name),
      `${name} is not in tools/list. The bridge came up and answered, but without it.`,
    );
  }
});

test("it reached NOTHING while doing so", async () => {
  const { stderr } = await once();
  // The seal is the point. A sealed child that still found a service would be reporting a successful
  // call to 127.0.0.1:8800 -- the operator's live fleet -- and this proof would have been bought by
  // registering into production. `127.0.0.2:1` is the only endpoint it was given.
  assert.ok(
    !/127\.0\.0\.1:8800/.test(stderr),
    `the child mentioned the operator's live service. Its endpoints were sealed to 127.0.0.2:1, so `
    + `it found 127.0.0.1:8800 some other way. stderr: ${stderr.slice(-2000)}`,
  );
});
