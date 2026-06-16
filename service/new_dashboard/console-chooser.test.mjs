#!/usr/bin/env node
// Tests for chooseSessionConsoleWidget — the pure Session-Console widget chooser. Imported
// DIRECTLY now (DASHBOARD_REBUILD_PLAN §0.6: the brittle regex + `new Function` source
// extraction is gone). Encodes the operator-reported 2026-05-24 widget oscillation fix and
// the Plan-4 Task-18 wrapper-PTY-over-synth preference.
//
// Run: node --test service/new_dashboard/console-chooser.test.mjs

import assert from "node:assert/strict";
import { test } from "node:test";
import { chooseSessionConsoleWidget, hermesGatewayUrlToHttp } from "./console-chooser.js";

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
  assert.equal(cache.get("sess1"), "vterm_abc");
});

test("xterm widget STAYS chosen when live id disappears but cache has prior value (Bug #3)", () => {
  const cache = new Map();
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
  const r = chooseSessionConsoleWidget({
    agent: makeAgent({}),
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
  assert.equal(r.isLive, false, "cached path should report isLive=false");
});

test("iframe widget chosen for hermes resident with gatewayUrl and no terminal cache", () => {
  const r = chooseSessionConsoleWidget({
    agent: makeAgent({}),
    sessionId: "sess-new",
    sessionMode: "resident",
    runtime: "hermes",
    runtimeConfig: { gatewayUrl: "ws://127.0.0.1:9119/api/ws?token=t" },
    cache: new Map(),
    hermesGatewayHttp: "http://127.0.0.1:9119/?token=t",
    codexAppServerUrl: "",
    codexThreadId: "",
    codexAttachable: false,
  });
  assert.equal(r.kind, "hermes-iframe");
  assert.equal(r.hermesGatewayHttp, "http://127.0.0.1:9119/?token=t");
});

test("codex-synth widget chosen for resident codex with appServerUrl and no terminal cache", () => {
  const r = chooseSessionConsoleWidget({
    agent: makeAgent({}),
    sessionId: "sess-codex",
    sessionMode: "resident",
    runtime: "codex",
    runtimeConfig: { appServerUrl: "ws://127.0.0.1:33839" },
    cache: new Map(),
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
  chooseSessionConsoleWidget({
    agent: makeAgent({ virtualTerminalId: "vterm_a" }), sessionId: "sess1", runtime: "hermes",
    runtimeConfig: {}, cache, hermesGatewayHttp: "http://x/", codexAppServerUrl: "", codexThreadId: "", codexAttachable: false,
  });
  chooseSessionConsoleWidget({
    agent: makeAgent({ virtualTerminalId: "vterm_b" }), sessionId: "sess2", runtime: "hermes",
    runtimeConfig: {}, cache, hermesGatewayHttp: "http://x/", codexAppServerUrl: "", codexThreadId: "", codexAttachable: false,
  });
  const r1 = chooseSessionConsoleWidget({
    agent: makeAgent({}), sessionId: "sess1", sessionMode: "managed", runtime: "hermes",
    runtimeConfig: {}, cache, hermesGatewayHttp: "http://x/", codexAppServerUrl: "", codexThreadId: "", codexAttachable: false,
  });
  assert.equal(r1.terminalId, "vterm_a", "sess1 should still see its own cached terminalId");
  const r2 = chooseSessionConsoleWidget({
    agent: makeAgent({ virtualTerminalId: "vterm_b" }), sessionId: "sess2", runtime: "hermes",
    runtimeConfig: {}, cache, hermesGatewayHttp: "http://x/", codexAppServerUrl: "", codexThreadId: "", codexAttachable: false,
  });
  assert.equal(r2.terminalId, "vterm_b", "sess2 should still see its own terminalId");
});

test("none widget when no terminal, no gateway, no codex app-server", () => {
  const r = chooseSessionConsoleWidget({
    agent: makeAgent({}), sessionId: "sess-none", runtime: "claude-code",
    runtimeConfig: {}, cache: new Map(), hermesGatewayHttp: "", codexAppServerUrl: "", codexThreadId: "", codexAttachable: false,
  });
  assert.equal(r.kind, "none");
});

test("prefers wrapper PTY over synth when both exist for same agent (Plan 4 Task 18)", () => {
  const r = chooseSessionConsoleWidget({
    agent: { runtime: "codex", runtimeState: { terminalId: "pty-1", virtualTerminalId: "synth-1" } },
    sessionId: "sess-both", runtime: "codex", runtimeConfig: {}, cache: new Map(),
    hermesGatewayHttp: "", codexAppServerUrl: "", codexThreadId: "", codexAttachable: false,
  });
  assert.equal(r.kind, "xterm");
  assert.equal(r.terminalId, "pty-1", `wrapper PTY must win over synth; got ${JSON.stringify(r)}`);
});

test("keeps synth when only synth exists (no wrapper) (Plan 4 Task 18)", () => {
  const r = chooseSessionConsoleWidget({
    agent: { runtime: "opencode", runtimeState: { virtualTerminalId: "synth-1" } },
    sessionId: "sess-synth-only", runtime: "opencode", runtimeConfig: {}, cache: new Map(),
    hermesGatewayHttp: "", codexAppServerUrl: "", codexThreadId: "", codexAttachable: false,
  });
  assert.equal(r.kind, "xterm");
  assert.equal(r.terminalId, "synth-1");
});

test("resident session with cached/stopping terminal does not render managed xterm", () => {
  const cache = new Map([["sess-resident", "old-managed-pty"]]);
  const r = chooseSessionConsoleWidget({
    agent: { runtime: "hermes", sessionMode: "resident", runtimeState: {} },
    sessionId: "sess-resident", sessionMode: "resident", terminalStatus: "stopping", runtime: "hermes",
    runtimeConfig: { gatewayUrl: "ws://127.0.0.1:9119/api/ws?token=t" }, cache,
    hermesGatewayHttp: "http://127.0.0.1:9119/?token=t", codexAppServerUrl: "", codexThreadId: "", codexAttachable: false,
  });
  assert.equal(r.kind, "hermes-iframe");
  assert.equal(r.terminalId, "");
});

test("managed Hermes with stale resident gateway does not render resident iframe", () => {
  const r = chooseSessionConsoleWidget({
    agent: { runtime: "hermes", sessionMode: "managed", runtimeState: {} },
    sessionId: "sess-managed-hermes", sessionMode: "managed", terminalStatus: "", runtime: "hermes",
    runtimeConfig: { gatewayUrl: "ws://127.0.0.1:9119/api/ws?token=t" }, cache: new Map(),
    hermesGatewayHttp: "http://127.0.0.1:9119/?token=t", codexAppServerUrl: "", codexThreadId: "", codexAttachable: false,
  });
  assert.equal(r.kind, "none");
});

test("managed Codex with stale resident app-server does not render resident synth console", () => {
  const r = chooseSessionConsoleWidget({
    agent: { runtime: "codex", sessionMode: "managed", runtimeState: {} },
    sessionId: "sess-managed-codex", sessionMode: "managed", terminalStatus: "", runtime: "codex",
    runtimeConfig: { appServerUrl: "ws://127.0.0.1:33839" }, cache: new Map(),
    hermesGatewayHttp: "", codexAppServerUrl: "ws://127.0.0.1:33839", codexThreadId: "thr-abc", codexAttachable: true,
  });
  assert.equal(r.kind, "none");
});

test("hermesGatewayUrlToHttp embeds loopback only, never a public host", () => {
  assert.equal(
    hermesGatewayUrlToHttp("ws://127.0.0.1:9119/api/ws?token=abc"),
    "http://127.0.0.1:9119/?token=abc",
  );
  assert.equal(hermesGatewayUrlToHttp("wss://example.com:9119/api/ws?token=abc"), "",
    "a public host must not be embedded (token leak)");
  assert.equal(hermesGatewayUrlToHttp("not-a-url"), "");
  assert.equal(hermesGatewayUrlToHttp(""), "");
});
