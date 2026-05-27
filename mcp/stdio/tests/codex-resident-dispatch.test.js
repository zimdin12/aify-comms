#!/usr/bin/env node
// End-to-end: resident-codex dispatch flow through createCodexControllerLegacy.
// Bridge → /dispatch/claim returns a resident run → launchRuntimeRun routes
// to createCodexController → resident + hasCodexLiveAppServer → LEGACY path →
// createWebSocketRpcClient connects to the local app-server → turn/start on
// residentThreadId → turn lifecycle notifications → mark dispatch delivered.
//
// This test pins the behavior so future churn (the codex-aify wrapper has
// been reverted+restored multiple times — see commits e7cac38, 2edbf2c,
// 1b8e0bf) can't silently break the resident dispatch path again.

import assert from "node:assert/strict";
import { test } from "node:test";
import path from "node:path";
import net from "node:net";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FAKE = path.join(__dirname, "fixtures", "fake-codex-app-server.mjs");

function pickFreePort() {
  return new Promise((resolve, reject) => {
    const srv = net.createServer();
    srv.unref();
    srv.on("error", reject);
    srv.listen(0, "127.0.0.1", () => {
      const port = srv.address().port;
      srv.close(() => resolve(port));
    });
  });
}

async function startFakeAppServer(t, { threadId, script = "hello" } = {}) {
  const port = await pickFreePort();
  const url = `ws://127.0.0.1:${port}`;
  const proc = spawn(process.execPath, [FAKE, "--listen", url], {
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, FAKE_CODEX_RESIDENT_THREAD: threadId || "", FAKE_CODEX_SCRIPT: script },
  });
  t.after(() => { try { proc.kill("SIGTERM"); } catch {} });
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("fake-codex did not bind in 5s")), 5000);
    proc.stdout.on("data", (chunk) => {
      if (String(chunk).includes("listening")) { clearTimeout(timeout); resolve(); }
    });
    proc.on("exit", (code) => reject(new Error(`fake-codex exited early code=${code}`)));
  });
  return { url, proc };
}

function makeResidentAgentInfo({ appServerUrl, threadId }) {
  return {
    agentId: "codex-resident-test",
    runtime: "codex",
    sessionMode: "resident",
    sessionHandle: threadId,
    cwd: process.cwd(),
    capabilities: ["resident-run"],
    runtimeConfig: { appServerUrl },
  };
}

function makeResidentRun(extra = {}) {
  return {
    id: "run_resident_test_001",
    executionMode: "resident",
    subject: "Test subject",
    body: "Hello from another agent via aify-comms.",
    from: "agent-a",
    ...extra,
  };
}

test("resident codex dispatch routes turn/start to local app-server", async (t) => {
  const threadId = "thr_resident_test_001";
  const { url } = await startFakeAppServer(t, { threadId });

  const { launchRuntimeRun } = await import("../runtimes.js");
  const events = [];
  const refs = [];
  const controller = launchRuntimeRun({
    agentId: "codex-resident-test",
    agentInfo: makeResidentAgentInfo({ appServerUrl: url, threadId }),
    run: makeResidentRun(),
    runtimeState: { threadId },
    callbacks: {
      onEvent: (kind, msg) => events.push({ kind, msg }),
      onRefs: (r) => refs.push(r),
    },
  });

  const result = await controller.promise.catch((err) => ({ failed: true, error: err?.message || String(err) }));
  assert.ok(!result.failed, `expected resident dispatch to succeed: ${result.error || ""}`);
  assert.equal(result.status, "completed", `expected status=completed, got ${result.status}`);

  // The bridge should have called onEvent with a "Started turn ..." entry
  // when the fake emitted turn/started.
  const turnStarts = events.filter((e) => e.kind === "turn" && /Started turn/.test(e.msg));
  assert.ok(turnStarts.length >= 1, `expected at least one 'Started turn' event, got: ${JSON.stringify(events).slice(0, 500)}`);

  // The refs callback should report the resident threadId so the dispatch
  // run is filed against the right thread.
  const turnRefs = refs.filter((r) => r && r.turnId);
  assert.ok(turnRefs.length >= 1, "expected at least one onRefs callback with a turnId");
});

test("resident codex dispatch echoes the prompt body into the synth-terminal sink", async (t) => {
  const threadId = "thr_resident_test_002";
  const { url } = await startFakeAppServer(t, { threadId });

  const frames = [];
  const sinkProvider = async () => async (text, status) => { frames.push({ text: String(text || ""), status: String(status || "") }); };

  const { launchRuntimeRun } = await import("../runtimes.js");
  const controller = launchRuntimeRun({
    agentId: "codex-resident-test",
    agentInfo: makeResidentAgentInfo({ appServerUrl: url, threadId }),
    run: makeResidentRun({ id: "run_resident_test_002", body: "Wake event payload" }),
    runtimeState: { threadId },
    callbacks: {
      onEvent: () => {},
      onRefs: () => {},
      terminalSinkProvider: sinkProvider,
    },
  });

  const result = await controller.promise.catch((err) => ({ failed: true, error: err?.message || String(err) }));
  assert.ok(!result.failed, `expected resident dispatch to succeed: ${result.error || ""}`);

  const allText = frames.map((f) => f.text).join("");
  assert.match(allText, /aify-comms message received/, "synth-terminal should show a codex-native aify-comms receipt marker");
  assert.match(allText, /Wake event payload/, "synth-terminal should echo the dispatch body");
  assert.match(allText, /turn started|turn ended/, "synth-terminal should reflect codex turn lifecycle");
});
