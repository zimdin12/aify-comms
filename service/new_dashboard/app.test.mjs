#!/usr/bin/env node
// Tests for chooseSessionConsoleWidget — pure helper that picks the
// Session Console widget and prevents the operator-reported 2026-05-24
// oscillation between iframe and xterm when the server temporarily
// clears runtime_state.virtualTerminalId.
//
// Run: node --test service/new_dashboard/app.test.mjs

import assert from "node:assert/strict";
import { test } from "node:test";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

// Extract the function from app.js by reading + module-fying it. The dashboard
// app.js is a browser script with top-level `state` and DOM refs — we can't
// import it directly. So we string-extract the exported function and eval it
// in an isolated context. This is acceptable for testing a pure helper.
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const appJsPath = path.join(__dirname, "app.js");
const source = fs.readFileSync(appJsPath, "utf8");
const fnMatch = source.match(/\nfunction chooseSessionConsoleWidget\([\s\S]*?\n\}\n/);
if (!fnMatch) throw new Error("chooseSessionConsoleWidget not found in app.js");
const wrapped = `${fnMatch[0]}\nreturn chooseSessionConsoleWidget;`;
const chooseSessionConsoleWidget = new Function(wrapped)();

function makeAgent({ virtualTerminalId = "", terminalId = "" } = {}) {
  return {
    runtime: "hermes",
    runtimeState: {
      ...(virtualTerminalId ? { virtualTerminalId } : {}),
      ...(terminalId ? { terminalId } : {}),
    },
  };
}

test("xterm widget chosen when live virtualTerminalId is present", () => {
  const cache = new Map();
  const r = chooseSessionConsoleWidget({
    agent: makeAgent({ virtualTerminalId: "vterm_abc" }),
    sessionId: "sess1",
    runtime: "hermes",
    runtimeConfig: {},
    cache,
    hermesGatewayHttp: "http://127.0.0.1:9119/?token=t",
    codexAppServerUrl: "",
    codexThreadId: "",
    codexAttachable: false,
  });
  assert.equal(r.kind, "xterm");
  assert.equal(r.terminalId, "vterm_abc");
  assert.equal(r.isLive, true);
  // Cache should now remember it for sess1.
  assert.equal(cache.get("sess1"), "vterm_abc");
});

test("xterm widget STAYS chosen when live id disappears but cache has prior value (Bug #3)", () => {
  const cache = new Map();
  // First render: live id present, populates cache.
  chooseSessionConsoleWidget({
    agent: makeAgent({ virtualTerminalId: "vterm_abc" }),
    sessionId: "sess1",
    runtime: "hermes",
    runtimeConfig: { gatewayUrl: "ws://127.0.0.1:9119/api/ws?token=t" },
    cache,
    hermesGatewayHttp: "http://127.0.0.1:9119/?token=t",
    codexAppServerUrl: "",
    codexThreadId: "",
    codexAttachable: false,
  });
  // Second render: server cleared virtualTerminalId (supersede or stop_agent_worker
  // race per Bug #3 Findings 3.5/3.6) — same session, no live id, but cache
  // remembers. Expected: still xterm, NOT iframe.
  const r = chooseSessionConsoleWidget({
    agent: makeAgent({}), // no terminal ids
    sessionId: "sess1",
    runtime: "hermes",
    runtimeConfig: { gatewayUrl: "ws://127.0.0.1:9119/api/ws?token=t" },
    cache,
    hermesGatewayHttp: "http://127.0.0.1:9119/?token=t",
    codexAppServerUrl: "",
    codexThreadId: "",
    codexAttachable: false,
  });
  assert.equal(r.kind, "xterm", `expected xterm to stick via cache; got ${r.kind}`);
  assert.equal(r.terminalId, "vterm_abc");
  assert.equal(r.isLive, false, "should report isLive=false so the renderer knows it's the cached path");
});

test("iframe widget chosen for hermes resident with gatewayUrl and no terminal cache", () => {
  const cache = new Map();
  const r = chooseSessionConsoleWidget({
    agent: makeAgent({}),
    sessionId: "sess-new",
    runtime: "hermes",
    runtimeConfig: { gatewayUrl: "ws://127.0.0.1:9119/api/ws?token=t" },
    cache,
    hermesGatewayHttp: "http://127.0.0.1:9119/?token=t",
    codexAppServerUrl: "",
    codexThreadId: "",
    codexAttachable: false,
  });
  assert.equal(r.kind, "hermes-iframe");
  assert.equal(r.hermesGatewayHttp, "http://127.0.0.1:9119/?token=t");
});

test("codex-synth widget chosen for resident codex with appServerUrl and no terminal cache", () => {
  const cache = new Map();
  const r = chooseSessionConsoleWidget({
    agent: makeAgent({}),
    sessionId: "sess-codex",
    runtime: "codex",
    runtimeConfig: { appServerUrl: "ws://127.0.0.1:33839" },
    cache,
    hermesGatewayHttp: "",
    codexAppServerUrl: "ws://127.0.0.1:33839",
    codexThreadId: "thr-abc",
    codexAttachable: true,
  });
  assert.equal(r.kind, "codex-synth");
  assert.equal(r.codexAppServerUrl, "ws://127.0.0.1:33839");
  assert.equal(r.codexThreadId, "thr-abc");
});

test("widget cache is per-session — switching session does NOT cross-contaminate", () => {
  const cache = new Map();
  // sess1 has terminal vterm_a, sess2 has terminal vterm_b.
  chooseSessionConsoleWidget({
    agent: makeAgent({ virtualTerminalId: "vterm_a" }),
    sessionId: "sess1",
    runtime: "hermes",
    runtimeConfig: {},
    cache,
    hermesGatewayHttp: "http://x/",
    codexAppServerUrl: "",
    codexThreadId: "",
    codexAttachable: false,
  });
  chooseSessionConsoleWidget({
    agent: makeAgent({ virtualTerminalId: "vterm_b" }),
    sessionId: "sess2",
    runtime: "hermes",
    runtimeConfig: {},
    cache,
    hermesGatewayHttp: "http://x/",
    codexAppServerUrl: "",
    codexThreadId: "",
    codexAttachable: false,
  });
  // Now sess1 live id disappears.
  const r1 = chooseSessionConsoleWidget({
    agent: makeAgent({}),
    sessionId: "sess1",
    runtime: "hermes",
    runtimeConfig: {},
    cache,
    hermesGatewayHttp: "http://x/",
    codexAppServerUrl: "",
    codexThreadId: "",
    codexAttachable: false,
  });
  assert.equal(r1.terminalId, "vterm_a", "sess1 should still see its own cached terminalId");
  const r2 = chooseSessionConsoleWidget({
    agent: makeAgent({ virtualTerminalId: "vterm_b" }),
    sessionId: "sess2",
    runtime: "hermes",
    runtimeConfig: {},
    cache,
    hermesGatewayHttp: "http://x/",
    codexAppServerUrl: "",
    codexThreadId: "",
    codexAttachable: false,
  });
  assert.equal(r2.terminalId, "vterm_b", "sess2 should still see its own terminalId");
});

test("none widget when no terminal, no gateway, no codex app-server", () => {
  const cache = new Map();
  const r = chooseSessionConsoleWidget({
    agent: makeAgent({}),
    sessionId: "sess-none",
    runtime: "claude-code",
    runtimeConfig: {},
    cache,
    hermesGatewayHttp: "",
    codexAppServerUrl: "",
    codexThreadId: "",
    codexAttachable: false,
  });
  assert.equal(r.kind, "none");
});
