# Hermes Resident Channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make resident-hermes agents reliably wake on inbound aify-comms messages, with mid-run insertion support — symmetric UX with `claude-channel.js` (notifications/claude/channel) and the codex resident dispatch path we just shipped. Operator gets a real Ink terminal TUI for `hermes chat --tui`; bridge injects messages over WebSocket so the running TUI renders them; no PTY paste, no scrambling, no upstream patches.

**Architecture:** `hermes-aify` (new wrapper, mirror of `codex-aify`) spawns `hermes dashboard --tui` in the background — this starts hermes's `_DASHBOARD_EMBEDDED_CHAT_ENABLED` web server on a free port, which mounts the `/api/ws` JSON-RPC endpoint (`hermes_cli/web_server.py:3526` and gates at `:3528` `:3560` `:3589`). The wrapper captures the ephemeral `_SESSION_TOKEN` from the dashboard's HTML response, then `exec`s `hermes chat --tui` in the operator's terminal with `HERMES_TUI_GATEWAY_URL=ws://127.0.0.1:<port>/api/ws?token=<token>` set in env. The Ink TUI's `gatewayClient.ts:resolveGatewayAttachUrl` reads that env var and `startAttachedGateway` opens a WebSocket to the running dashboard's gateway instead of spawning its own stdio sidecar (`ui-tui/src/gatewayClient.ts:32-36, 404-498, 500-525`). The aify-comms bridge also opens a WebSocket to the same `/api/ws`, and `TeeTransport` (`tui_gateway/transport.py`) fans out dispatcher events to both attached clients. For inbound aify-comms messages the bridge calls `prompt.submit` (idle) or `session.steer` (busy) — `tui_gateway/server.py:3140` and `:3103` respectively. Mid-run insertion comes free because `session.steer` is the native primitive for "inject text into running turn without interrupt."

**Tech Stack:** Bash wrapper (install.sh), Node.js stdio bridge (`@modelcontextprotocol/sdk`, `ws`), Python FastAPI backend, `node:test`.

---

## File Structure

| Path | Responsibility |
|------|----------------|
| `install.sh` (modify hermes-aify section, ~lines 590-665) | Rewrite `hermes-aify` wrapper. Mirror of codex-aify (install.sh:319-424): pick free port, launch `hermes dashboard --port $P --no-browser` (verify flag in Task 1) as background child, wait for readiness, fetch `/` and parse `__HERMES_SESSION_TOKEN__` from HTML, export `HERMES_TUI_GATEWAY_URL` + `AIFY_HERMES_GATEWAY_URL` + `AIFY_HERMES_GATEWAY_TOKEN`, cleanup trap kills the dashboard child on exit, `exec hermes chat --tui` for the operator's TUI. |
| `mcp/stdio/server.js` (modify, around the codex marker block at :170-183) | When `AIFY_HERMES_GATEWAY_URL` is set in env, write a `hermes` runtime marker with `gatewayUrl` + `gatewayToken`. Mirror of the existing codex marker write. |
| `mcp/stdio/runtimes.js` (extend `createHermesController` at ~line 3533) | Add a resident-channel branch: when `executionMode === "resident"` AND `runtimeConfig.gatewayUrl` is set, connect WS to the gateway with the token query param, call `prompt.submit` (or `session.steer` for in-flight turns), translate dispatch into the gateway's JSON-RPC frame shape, listen for response events, push synth-terminal frames. Mirror of `createCodexControllerLegacy` resident path. |
| `mcp/stdio/hermes-gateway-protocol.js` (create) | Thin protocol helper. Builds the `prompt.submit` / `session.steer` / `session.list` / `session.most_recent` JSON-RPC frames. Translates inbound gateway events (`agent.message.delta`, `agent.message.end`, `tool.started`, `tool.completed`, `error`) into the bridge's existing `onEvent` / synth-terminal frame shape. Keeps `createHermesController` thin. |
| `mcp/stdio/tests/hermes-gateway-protocol.test.js` (create) | Unit tests: frame construction + event translation. |
| `mcp/stdio/tests/hermes-resident-dispatch.test.js` (create) | End-to-end: fake hermes gateway WS server, simulated dispatch, assert `prompt.submit` reaches the gateway with the right shape, assert synth-terminal frames are pushed. |
| `mcp/stdio/tests/fixtures/fake-hermes-gateway.mjs` (create) | WebSocket server fixture mimicking `tui_gateway/ws.py` — accepts `prompt.submit` / `session.steer` / `session.list` / `session.most_recent`, streams agent.message.delta + agent.message.end. |
| `install.hermes.md` (modify) | Replace the current copy-paste flow with the new resident-channel flow. Document the dashboard-as-background-process pattern, the no-browser caveat, and how to verify. |
| `DECISIONS.md` (append) | New section: "Resident hermes uses `hermes dashboard --tui` as a background gateway server + `HERMES_TUI_GATEWAY_URL` attach." |
| `mcp/stdio/runtime-markers.js` (touch only if marker schema needs extending) | Probably no change — `writeRuntimeMarker("hermes", cwd, {gatewayUrl, gatewayToken})` should work via the existing arbitrary-data path. |

---

## Task 1: Upstream flag validation probe (no code change)

The plan assumes specific `hermes dashboard` flags. Run them once on the operator's machine to confirm the exact spelling before we write code against them.

**Files:** None (interactive verification).

- [ ] **Step 1: Confirm `hermes dashboard --help`**

Run: `hermes dashboard --help`
Expected output includes flags for: `--port <N>`, `--no-browser` (or `--no-open`), `--tui` (already known), `--host`.

Record the exact spelling for each. If `--no-browser` doesn't exist, look for `--headless` / `--open-browser=false` / a `HERMES_NO_BROWSER=1` env override.

- [ ] **Step 2: Confirm `hermes web --help` (alternate entry)**

Run: `hermes web --help` (per `web_server.py:8` docstring it's an alternate entry — may be simpler than `dashboard`).

Pick whichever (a) accepts `--port`, (b) accepts `--no-browser` or has an env-var equivalent, (c) mounts `/api/ws` (i.e. enables `embedded_chat=True`). The wrapper will use that one.

- [ ] **Step 3: Confirm `hermes chat --tui` attaches via `HERMES_TUI_GATEWAY_URL`**

In one terminal:
```bash
hermes dashboard --tui --port 9119 --no-browser   # or hermes web --tui --port 9119
```

In another terminal — first probe the auth header expectation:
```bash
curl -s http://127.0.0.1:9119/ | grep -o '__HERMES_SESSION_TOKEN__="[^"]*"' | head -1
```
Should print one token line. Capture the token.

Now launch the chat with the attach URL pointing at the running dashboard:
```bash
export HERMES_TUI_GATEWAY_URL="ws://127.0.0.1:9119/api/ws?token=<TOKEN>"
hermes chat --tui
```

Expected: Ink TUI starts in the terminal AND the operator's chat shares state with anything else attached. Run any prompt — confirm responses stream in the terminal TUI normally.

- [ ] **Step 4: Confirm bridge-injection works manually**

While the chat from Step 3 is still running, in a third terminal:
```bash
node -e "
const WS = require('ws');
const ws = new WS('ws://127.0.0.1:9119/api/ws?token=<TOKEN>');
ws.on('open', () => {
  ws.send(JSON.stringify({
    jsonrpc: '2.0',
    id: 1,
    method: 'session.most_recent',
    params: {}
  }));
});
ws.on('message', msg => {
  const d = JSON.parse(msg.toString());
  console.log('reply:', JSON.stringify(d).slice(0, 300));
  if (d.id === 1 && d.result?.session_id) {
    ws.send(JSON.stringify({
      jsonrpc: '2.0',
      id: 2,
      method: 'prompt.submit',
      params: { session_id: d.result.session_id, text: 'Hello from bridge injection probe' }
    }));
  }
});
"
```

Expected:
- The probe sees a `result.session_id` reply.
- The operator's Ink TUI (Step 3 terminal) renders "Hello from bridge injection probe" as a user turn AND the model's reply streams in.
- No keyboard scrambling, no PTY weirdness.

If this works, the architecture is confirmed and Task 2-onward is safe to implement. If it doesn't (e.g. `session.most_recent` doesn't exist, or `prompt.submit` errors), record the actual method names / errors and revise the plan.

- [ ] **Step 5: Commit observations (no code yet)**

If anything in Steps 1-4 doesn't match the plan assumptions, update `docs/plans/2026-05-24-hermes-resident-channel.md` with the actual flag / method names. Otherwise no commit.

---

## Task 2: Fake hermes gateway WS fixture

Build the test double we need for Tasks 3+5.

**Files:**
- Create: `mcp/stdio/tests/fixtures/fake-hermes-gateway.mjs`
- Reference: `mcp/stdio/tests/fixtures/fake-codex-app-server.mjs` (the `--listen` WS mode we shipped in commit `6a34a1f`)

- [ ] **Step 1: Write the fixture**

Create `mcp/stdio/tests/fixtures/fake-hermes-gateway.mjs`:

```javascript
#!/usr/bin/env node
// Test double for hermes's tui_gateway WebSocket. Speaks JSON-RPC 2.0
// matching the subset of methods used by the aify-comms hermes resident
// channel: session.list, session.most_recent, prompt.submit, session.steer.
// Streams agent.message.delta / agent.message.end / tool.started /
// tool.completed events back over the same socket.
//
// Scripts (env FAKE_HERMES_SCRIPT):
//   - hello (default): one prompt.submit → "hello from hermes" stream → end
//   - busy           : prompt.submit returns 4009 "session busy"; session.steer accepted
//   - refuse         : prompt.submit returns 5000 error
//   - crash-on-init  : exit(1) on first frame

import { WebSocketServer } from "ws";

const SCRIPT = String(process.env.FAKE_HERMES_SCRIPT || "hello");
const DELAY_MS = Number(process.env.FAKE_HERMES_DELAY_MS || 5);
const FIXED_SESSION_ID = process.env.FAKE_HERMES_SESSION_ID || "sess-fake-001";

const cliArgs = process.argv.slice(2);
const listenIdx = cliArgs.indexOf("--listen");
const LISTEN_URL = listenIdx >= 0 ? String(cliArgs[listenIdx + 1] || "") : "";
if (!LISTEN_URL) { console.error("--listen ws://... required"); process.exit(2); }

const TOKEN = process.env.FAKE_HERMES_TOKEN || "test-token";

function delay(ms) { return new Promise((r) => setTimeout(r, ms)); }

const url = new URL(LISTEN_URL);
const wss = new WebSocketServer({ port: Number(url.port), host: url.hostname || "127.0.0.1" });
wss.on("listening", () => process.stdout.write(`fake-hermes-gateway listening on ${LISTEN_URL}\n`));

wss.on("connection", (socket, req) => {
  if (SCRIPT === "crash-on-init") { process.exit(1); }
  const reqUrl = new URL(req.url, "ws://localhost");
  const presentedToken = reqUrl.searchParams.get("token");
  if (presentedToken !== TOKEN) {
    socket.close(4001, "bad token");
    return;
  }

  const send = (obj) => { try { socket.send(JSON.stringify(obj)); } catch {} };

  socket.on("message", async (frame) => {
    let msg;
    try { msg = JSON.parse(String(frame)); } catch { return; }

    if (msg.method === "session.most_recent") {
      send({ jsonrpc: "2.0", id: msg.id, result: { session_id: FIXED_SESSION_ID } });
      return;
    }
    if (msg.method === "session.list") {
      send({ jsonrpc: "2.0", id: msg.id, result: { sessions: [{ id: FIXED_SESSION_ID, title: "fake" }] } });
      return;
    }
    if (msg.method === "session.steer") {
      send({ jsonrpc: "2.0", id: msg.id, result: { status: "queued", text: msg.params?.text || "" } });
      return;
    }
    if (msg.method === "prompt.submit") {
      if (SCRIPT === "busy") {
        send({ jsonrpc: "2.0", id: msg.id, error: { code: 4009, message: "session busy" } });
        return;
      }
      if (SCRIPT === "refuse") {
        send({ jsonrpc: "2.0", id: msg.id, error: { code: 5000, message: "policy denied" } });
        return;
      }
      send({ jsonrpc: "2.0", id: msg.id, result: { status: "streaming" } });
      const sid = msg.params?.session_id || FIXED_SESSION_ID;
      // Stream a hermes-shaped reply.
      for (const chunk of ["hello", " ", "from", " ", "hermes"]) {
        await delay(DELAY_MS);
        send({ jsonrpc: "2.0", method: "agent.message.delta", params: { session_id: sid, delta: chunk } });
      }
      await delay(DELAY_MS);
      send({ jsonrpc: "2.0", method: "agent.message.end", params: { session_id: sid, text: "hello from hermes" } });
      return;
    }
    if (msg.id !== undefined) {
      send({ jsonrpc: "2.0", id: msg.id, error: { code: -32601, message: `method not found: ${msg.method}` } });
    }
  });
});
```

- [ ] **Step 2: Quick smoke test that the fixture starts**

Run:
```bash
node mcp/stdio/tests/fixtures/fake-hermes-gateway.mjs --listen ws://127.0.0.1:30200 &
sleep 1
echo "smoke: connection-list output:"
node -e "
const WS = require('ws');
const ws = new WS('ws://127.0.0.1:30200/api/ws?token=test-token');
ws.on('open', () => ws.send(JSON.stringify({jsonrpc:'2.0',id:1,method:'session.most_recent',params:{}})));
ws.on('message', m => { console.log('reply', String(m)); process.exit(0); });
"
kill %1 2>/dev/null || true
```
Expected: see `reply {"jsonrpc":"2.0","id":1,"result":{"session_id":"sess-fake-001"}}`.

- [ ] **Step 3: Commit**

```bash
git add mcp/stdio/tests/fixtures/fake-hermes-gateway.mjs
git commit -m "test(hermes-resident): fake hermes tui_gateway WS fixture"
```

---

## Task 3: hermes-gateway-protocol helper

Build the thin protocol module that translates aify dispatches into gateway JSON-RPC frames and gateway events into bridge callbacks.

**Files:**
- Create: `mcp/stdio/hermes-gateway-protocol.js`
- Create: `mcp/stdio/tests/hermes-gateway-protocol.test.js`

- [ ] **Step 1: Write the failing protocol test**

Create `mcp/stdio/tests/hermes-gateway-protocol.test.js`:

```javascript
#!/usr/bin/env node
import assert from "node:assert/strict";
import { test } from "node:test";
import {
  buildPromptSubmitFrame,
  buildSessionSteerFrame,
  buildSessionMostRecentFrame,
  translateGatewayEvent,
} from "../hermes-gateway-protocol.js";

test("buildPromptSubmitFrame produces a valid JSON-RPC 2.0 prompt.submit", () => {
  const frame = buildPromptSubmitFrame({ id: 7, sessionId: "sess-1", text: "hi" });
  assert.equal(frame.jsonrpc, "2.0");
  assert.equal(frame.id, 7);
  assert.equal(frame.method, "prompt.submit");
  assert.deepEqual(frame.params, { session_id: "sess-1", text: "hi" });
});

test("buildSessionSteerFrame produces a valid JSON-RPC 2.0 session.steer", () => {
  const frame = buildSessionSteerFrame({ id: 8, sessionId: "sess-1", text: "mid-run nudge" });
  assert.equal(frame.method, "session.steer");
  assert.deepEqual(frame.params, { session_id: "sess-1", text: "mid-run nudge" });
});

test("buildSessionMostRecentFrame is a parameter-less most-recent lookup", () => {
  const frame = buildSessionMostRecentFrame({ id: 1 });
  assert.equal(frame.method, "session.most_recent");
  assert.deepEqual(frame.params, {});
});

test("translateGatewayEvent maps agent.message.delta to a delta event", () => {
  const out = translateGatewayEvent({ jsonrpc: "2.0", method: "agent.message.delta", params: { delta: "abc" } });
  assert.deepEqual(out, { kind: "delta", text: "abc" });
});

test("translateGatewayEvent maps agent.message.end to a final event with text", () => {
  const out = translateGatewayEvent({ jsonrpc: "2.0", method: "agent.message.end", params: { text: "done" } });
  assert.deepEqual(out, { kind: "final", text: "done" });
});

test("translateGatewayEvent maps error to an error event", () => {
  const out = translateGatewayEvent({ jsonrpc: "2.0", method: "error", params: { message: "boom" } });
  assert.deepEqual(out, { kind: "error", text: "boom" });
});

test("translateGatewayEvent returns null for unknown methods", () => {
  const out = translateGatewayEvent({ jsonrpc: "2.0", method: "telemetry.something", params: {} });
  assert.equal(out, null);
});
```

- [ ] **Step 2: Run test — expect FAIL (module doesn't exist)**

Run: `node --test mcp/stdio/tests/hermes-gateway-protocol.test.js`
Expected: FAIL with `Cannot find module '../hermes-gateway-protocol.js'`.

- [ ] **Step 3: Implement `hermes-gateway-protocol.js`**

Create `mcp/stdio/hermes-gateway-protocol.js`:

```javascript
// Pure functions for translating between aify-comms dispatch shapes and
// hermes tui_gateway JSON-RPC 2.0 frames over WebSocket. No side effects;
// no I/O. The session controller in runtimes.js owns the WS connection
// and uses these to build outbound frames + translate inbound events.

export function buildPromptSubmitFrame({ id, sessionId, text }) {
  return {
    jsonrpc: "2.0",
    id,
    method: "prompt.submit",
    params: { session_id: String(sessionId || ""), text: String(text || "") },
  };
}

export function buildSessionSteerFrame({ id, sessionId, text }) {
  return {
    jsonrpc: "2.0",
    id,
    method: "session.steer",
    params: { session_id: String(sessionId || ""), text: String(text || "") },
  };
}

export function buildSessionMostRecentFrame({ id }) {
  return {
    jsonrpc: "2.0",
    id,
    method: "session.most_recent",
    params: {},
  };
}

export function buildSessionListFrame({ id }) {
  return {
    jsonrpc: "2.0",
    id,
    method: "session.list",
    params: {},
  };
}

export function translateGatewayEvent(message) {
  const method = String(message?.method || "");
  const params = message?.params || {};
  if (method === "agent.message.delta") {
    return { kind: "delta", text: String(params.delta || "") };
  }
  if (method === "agent.message.end") {
    return { kind: "final", text: String(params.text || "") };
  }
  if (method === "tool.started") {
    return { kind: "tool_started", label: String(params.tool || params.name || "tool") };
  }
  if (method === "tool.completed") {
    return { kind: "tool_completed", label: String(params.tool || params.name || "tool") };
  }
  if (method === "error") {
    return { kind: "error", text: String(params.message || "") };
  }
  return null;
}
```

- [ ] **Step 4: Run test — expect PASS**

Run: `node --test mcp/stdio/tests/hermes-gateway-protocol.test.js`
Expected: 7 PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp/stdio/hermes-gateway-protocol.js mcp/stdio/tests/hermes-gateway-protocol.test.js
git commit -m "feat(hermes-resident): pure protocol module for tui_gateway WS frames"
```

---

## Task 4: Wire `hermes` runtime marker in server.js

When the bridge starts inside a `hermes-aify` session, write a runtime marker so the aify-comms backend can route resident-hermes dispatches to this bridge.

**Files:**
- Modify: `mcp/stdio/server.js` (codex marker block at ~lines 170-183)

- [ ] **Step 1: Read the existing codex marker block**

Look at `mcp/stdio/server.js:163-183`. Mirror that pattern: read `AIFY_HERMES_GATEWAY_URL` (+ optional `AIFY_HERMES_GATEWAY_TOKEN`) from env; if present, write a `hermes` runtime marker.

- [ ] **Step 2: Insert the marker write**

Add immediately after the codex marker block:

```javascript
// Write the Hermes runtime marker from this long-lived bridge process when
// we detect we are running inside a hermes-aify wrapper (which sets the
// AIFY_HERMES_GATEWAY_URL environment variable before launching hermes
// chat). Mirror of the codex marker write above.
const AIFY_HERMES_GATEWAY_URL = String(process.env.AIFY_HERMES_GATEWAY_URL || "").trim();
const AIFY_HERMES_GATEWAY_TOKEN_ENV = String(process.env.AIFY_HERMES_GATEWAY_TOKEN_ENV || "AIFY_HERMES_GATEWAY_TOKEN").trim();
let hermesMarkerCwd = "";
if (AIFY_HERMES_GATEWAY_URL) {
  hermesMarkerCwd = DEFAULT_CWD;
  try {
    const markerData = { gatewayUrl: AIFY_HERMES_GATEWAY_URL };
    if (AIFY_HERMES_GATEWAY_TOKEN_ENV) markerData.gatewayTokenEnv = AIFY_HERMES_GATEWAY_TOKEN_ENV;
    writeRuntimeMarker("hermes", hermesMarkerCwd, markerData);
  } catch (error) {
    console.error("[aify] failed to write hermes runtime marker:", error?.message || String(error));
    hermesMarkerCwd = "";
  }
}
```

And mirror the cleanup in `cleanupOnExit()`:

```javascript
// Add near the codex marker cleanup
if (hermesMarkerCwd) {
  try { removeRuntimeMarker("hermes", hermesMarkerCwd); } catch { /* best effort */ }
}
```

- [ ] **Step 3: Smoke check**

Run: `node --check mcp/stdio/server.js`
Expected: no syntax errors.

- [ ] **Step 4: Commit**

```bash
git add mcp/stdio/server.js
git commit -m "feat(hermes-resident): write hermes runtime marker when AIFY_HERMES_GATEWAY_URL is set"
```

---

## Task 5: End-to-end resident-hermes dispatch test + controller branch

The interesting work — extend `createHermesController` (runtimes.js:3533) with a resident-channel branch that talks to the gateway WS.

**Files:**
- Modify: `mcp/stdio/runtimes.js` (createHermesController + add gateway helpers)
- Create: `mcp/stdio/tests/hermes-resident-dispatch.test.js`

- [ ] **Step 1: Write the failing test**

Create `mcp/stdio/tests/hermes-resident-dispatch.test.js`:

```javascript
#!/usr/bin/env node
// E2E: resident hermes dispatch via tui_gateway WS.
// Bridge → /dispatch/claim returns resident run → launchRuntimeRun routes
// to createHermesController → resident + runtimeConfig.gatewayUrl set →
// WS connect → session.most_recent → prompt.submit → agent.message.delta/end
// → mark dispatch delivered.

import assert from "node:assert/strict";
import { test } from "node:test";
import path from "node:path";
import net from "node:net";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FAKE = path.join(__dirname, "fixtures", "fake-hermes-gateway.mjs");

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

async function startFake(t, { script = "hello", token = "test-token" } = {}) {
  const port = await pickFreePort();
  const url = `ws://127.0.0.1:${port}`;
  const proc = spawn(process.execPath, [FAKE, "--listen", url], {
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, FAKE_HERMES_SCRIPT: script, FAKE_HERMES_TOKEN: token },
  });
  t.after(() => { try { proc.kill("SIGTERM"); } catch {} });
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("fake-hermes did not bind in 5s")), 5000);
    proc.stdout.on("data", (c) => { if (String(c).includes("listening")) { clearTimeout(timeout); resolve(); } });
    proc.on("exit", (code) => reject(new Error(`fake-hermes exited early code=${code}`)));
  });
  return { url, token };
}

test("resident hermes dispatch routes prompt.submit to tui_gateway WS", async (t) => {
  const { url, token } = await startFake(t);
  const attachUrl = `${url}/api/ws?token=${token}`;

  const { launchRuntimeRun } = await import("../runtimes.js");
  const events = [];
  const frames = [];
  const sinkProvider = async () => async (text, status) => { frames.push({ text: String(text || ""), status: String(status || "") }); };

  const controller = launchRuntimeRun({
    agentId: "hermes-resident-test",
    agentInfo: {
      agentId: "hermes-resident-test",
      runtime: "hermes",
      sessionMode: "resident",
      cwd: process.cwd(),
      capabilities: ["resident-run"],
      runtimeConfig: { gatewayUrl: attachUrl },
    },
    run: { id: "run_h_001", executionMode: "resident", subject: "Wake test", body: "Hello hermes", from: "agent-a" },
    runtimeState: {},
    callbacks: {
      onEvent: (kind, msg) => events.push({ kind, msg }),
      onRefs: () => {},
      terminalSinkProvider: sinkProvider,
    },
  });

  const result = await controller.promise.catch((err) => ({ failed: true, error: err?.message || String(err) }));
  assert.ok(!result.failed, `expected resident hermes dispatch to succeed: ${result.error || ""}`);
  assert.equal(result.status, "completed");
  assert.match(result.summary || "", /hello from hermes/);

  const allText = frames.map((f) => f.text).join("");
  assert.match(allText, /Hello hermes/, "synth-terminal should echo dispatch body");
  assert.match(allText, /hello from hermes/, "synth-terminal should reflect streamed reply");
});

test("resident hermes uses session.steer when prompt.submit reports session busy", async (t) => {
  const { url, token } = await startFake(t, { script: "busy" });
  const attachUrl = `${url}/api/ws?token=${token}`;

  const { launchRuntimeRun } = await import("../runtimes.js");
  const events = [];

  const controller = launchRuntimeRun({
    agentId: "hermes-resident-busy",
    agentInfo: {
      agentId: "hermes-resident-busy",
      runtime: "hermes",
      sessionMode: "resident",
      cwd: process.cwd(),
      capabilities: ["resident-run"],
      runtimeConfig: { gatewayUrl: attachUrl },
    },
    run: { id: "run_h_002", executionMode: "resident", subject: "Mid-run", body: "mid-run text", from: "agent-a" },
    runtimeState: {},
    callbacks: {
      onEvent: (kind, msg) => events.push({ kind, msg }),
      onRefs: () => {},
    },
  });

  const result = await controller.promise.catch((err) => ({ failed: true, error: err?.message || String(err) }));
  assert.ok(!result.failed, `expected steer-fallback to succeed: ${result.error || ""}`);
  const steerEvents = events.filter((e) => /steer/i.test(e.msg || ""));
  assert.ok(steerEvents.length >= 1, "expected at least one steer-related event when prompt.submit reports busy");
});
```

- [ ] **Step 2: Run test — expect FAIL (controller doesn't have resident-gateway branch yet)**

Run: `node --test mcp/stdio/tests/hermes-resident-dispatch.test.js`
Expected: FAIL — `createHermesController` currently has no `runtimeConfig.gatewayUrl` branch.

- [ ] **Step 3: Add the resident-gateway branch to createHermesController**

In `mcp/stdio/runtimes.js`, locate `createHermesController` (~line 3533). Add a branch BEFORE the existing managed/resident routing:

```javascript
function createHermesController({ agentId, agentInfo, run, runtimeState, callbacks }) {
  const executionMode = String(run.executionMode || agentInfo.sessionMode || "managed").trim().toLowerCase();
  const cfg = getRuntimeConfig(agentInfo);
  const gatewayUrl = String(cfg.gatewayUrl || "").trim();
  if (executionMode === "resident" && /^wss?:\/\//i.test(gatewayUrl)) {
    return createHermesResidentChannelController({ agentId, agentInfo, run, runtimeState, callbacks });
  }
  // ... existing routing ...
}
```

Then implement `createHermesResidentChannelController` immediately below `createHermesController`:

```javascript
function createHermesResidentChannelController({ agentId, agentInfo, run, runtimeState, callbacks }) {
  const cfg = getRuntimeConfig(agentInfo);
  const gatewayUrl = String(cfg.gatewayUrl || "").trim();
  const timeoutMs = Number(cfg.timeoutMs || 12 * 60 * 60 * 1000);

  let finalText = "";
  let settled = false;
  let rpc = null;
  let nextId = 100;
  let resolvePromise, rejectPromise;
  const pending = new Map();

  let terminalSink = null;
  let sinkChain = Promise.resolve();
  const pushTerminalFrame = (text, status = "") => {
    try {
      if (!terminalSink || (!text && !status)) return;
      const frame = { text: String(text || ""), status: String(status || "") };
      sinkChain = sinkChain.then(async () => { try { await terminalSink(frame.text, frame.status); } catch {} });
    } catch {}
  };

  const promise = new Promise(async (resolve, reject) => {
    resolvePromise = resolve;
    rejectPromise = reject;
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      reject(new Error(`Hermes resident channel timed out after ${timeoutMs}ms`));
    }, timeoutMs);

    if (typeof callbacks?.terminalSinkProvider === "function") {
      try {
        const sink = await callbacks.terminalSinkProvider({ agentId, agentInfo });
        if (typeof sink === "function") terminalSink = sink;
      } catch {}
    }

    // Echo prompt body into synth terminal
    try {
      const body = String(run?.body || "").trim();
      const subject = String(run?.subject || "").trim();
      const from = String(run?.from || "dashboard").trim() || "dashboard";
      const header = subject ? `\r\n\x1b[92m>\x1b[0m [${from}] ${subject}\r\n` : `\r\n\x1b[92m>\x1b[0m [${from}]\r\n`;
      const prefixed = body.split(/\r?\n/).map((l) => `\x1b[92m>\x1b[0m ${l}`).join("\r\n");
      pushTerminalFrame(`${header}${prefixed}\r\n`, "running");
      pushTerminalFrame("\x1b[2m[hermes] connecting...\x1b[0m\r\n", "running");
    } catch {}

    try {
      const {
        buildPromptSubmitFrame,
        buildSessionSteerFrame,
        buildSessionMostRecentFrame,
        translateGatewayEvent,
      } = await import("./hermes-gateway-protocol.js");

      // Open WS with token already in query string per gatewayUrl
      const socket = new WebSocket(gatewayUrl);
      rpc = socket;

      const sendRpc = (frame) => new Promise((resolveReq, rejectReq) => {
        const id = frame.id ?? (nextId++);
        frame.id = id;
        const timer = setTimeout(() => {
          pending.delete(id);
          rejectReq(new Error(`hermes RPC ${frame.method} timed out`));
        }, 60000);
        pending.set(id, {
          resolve: (v) => { clearTimeout(timer); resolveReq(v); },
          reject: (e) => { clearTimeout(timer); rejectReq(e); },
        });
        socket.send(JSON.stringify(frame));
      });

      socket.on("message", (raw) => {
        let msg;
        try { msg = JSON.parse(String(raw)); } catch { return; }
        if (msg.id !== undefined && pending.has(msg.id)) {
          const pend = pending.get(msg.id);
          pending.delete(msg.id);
          if (msg.error) pend.reject(new Error(msg.error.message || "RPC error"));
          else pend.resolve(msg.result);
          return;
        }
        const ev = translateGatewayEvent(msg);
        if (!ev) return;
        if (ev.kind === "delta") {
          finalText += ev.text;
          pushTerminalFrame(ev.text);
        } else if (ev.kind === "final") {
          finalText = ev.text || finalText;
          pushTerminalFrame(`\r\n\x1b[36m\x1b[1m■ turn ended\x1b[0m\r\n`);
          if (!settled) {
            settled = true;
            clearTimeout(timer);
            resolve({ status: "completed", summary: finalText.trim() || "(no output)", runtimeState: {}, externalRefs: {} });
          }
        } else if (ev.kind === "error") {
          pushTerminalFrame(`\r\n\x1b[31m\x1b[1m✗ error\x1b[0m ${ev.text}\r\n`);
        }
      });
      socket.on("close", () => {
        if (!settled) {
          settled = true;
          clearTimeout(timer);
          reject(new Error("Hermes gateway websocket closed before turn completed"));
        }
      });
      socket.on("error", (err) => {
        if (!settled) {
          settled = true;
          clearTimeout(timer);
          reject(err);
        }
      });

      await new Promise((res, rej) => {
        socket.once("open", res);
        socket.once("error", rej);
      });

      // Resolve sessionId — sessionHandle wins, else session.most_recent
      let sessionId = String(agentInfo?.sessionHandle || "").trim();
      if (!sessionId) {
        const mostRecent = await sendRpc(buildSessionMostRecentFrame({})).catch(() => null);
        sessionId = String(mostRecent?.session_id || "").trim();
      }
      if (!sessionId) throw new Error("Hermes gateway has no resolvable sessionId — operator should start a chat first");
      callbacks.onRefs?.({ sessionId });

      const body = String(run?.body || "").trim();
      // Try prompt.submit first; on 4009 busy, fall back to session.steer
      try {
        await sendRpc(buildPromptSubmitFrame({ sessionId, text: body }));
        callbacks.onEvent?.("hermes", `prompt.submit accepted on session ${sessionId}`);
      } catch (err) {
        const msg = String(err?.message || "");
        if (/session busy/i.test(msg) || /4009/.test(msg)) {
          callbacks.onEvent?.("hermes", `prompt.submit busy; falling back to session.steer on ${sessionId}`);
          await sendRpc(buildSessionSteerFrame({ sessionId, text: body }));
          callbacks.onEvent?.("hermes", `session.steer queued on session ${sessionId}`);
          if (!settled) {
            settled = true;
            clearTimeout(timer);
            resolve({ status: "completed", summary: `Steered into running turn: ${body.slice(0, 80)}`, runtimeState: {}, externalRefs: { sessionId } });
          }
        } else {
          throw err;
        }
      }
    } catch (error) {
      if (!settled) {
        settled = true;
        reject(error);
      }
    }
  });

  return {
    capabilities: { interrupt: true, steer: true },
    interrupt: () => {
      if (rpc && rpc.close) rpc.close();
    },
    steer: async (text) => {
      // No-op for now — caller goes through bridge dispatch path again.
      // Future: open new WS, call session.steer directly.
      throw new Error("Direct steer not implemented for resident-hermes channel; send a new comms_send instead");
    },
    promise,
  };
}
```

- [ ] **Step 4: Run test — expect PASS**

Run: `node --test mcp/stdio/tests/hermes-resident-dispatch.test.js`
Expected: 2 PASS.

- [ ] **Step 5: Run the broader hermes test suite**

Run:
```bash
node --test mcp/stdio/tests/hermes-acp-protocol.test.js mcp/stdio/tests/hermes-runtime.test.js mcp/stdio/tests/hermes-session-acp.test.js
```
Expected: all PASS (no regressions in the managed-hermes path).

- [ ] **Step 6: Commit**

```bash
git add mcp/stdio/runtimes.js mcp/stdio/tests/hermes-resident-dispatch.test.js
git commit -m "feat(hermes-resident): tui_gateway WS controller + prompt.submit/session.steer fallback"
```

---

## Task 6: Rewrite the `hermes-aify` wrapper in install.sh

Now the bridge side is ready. Make `hermes-aify` launch the dashboard, capture the token, attach the chat TUI.

**Files:**
- Modify: `install.sh` (hermes wrapper generation, ~lines 590-665 — locate `case "$cli" in ... hermes)`)

- [ ] **Step 1: Locate the hermes wrapper generation block**

Run: `grep -n "hermes-aify\|HERMES_RUNTIME_COMMAND\|hermes)" install.sh`
Identify the block that writes the hermes-aify shell script (mirror of codex-aify section at install.sh:319-424).

- [ ] **Step 2: Rewrite to launch dashboard + capture token + attach chat**

Replace the existing hermes-aify wrapper generation with:

```bash
# Mirror of codex-aify (install.sh:319-424):
#   1. Pick a free port for the hermes dashboard (which mounts /api/ws when
#      embedded_chat=True).
#   2. Spawn `hermes dashboard --port $P --no-browser` (or `hermes web ...`,
#      whichever Task 1 validated) in background.
#   3. Wait for it to be reachable, fetch /, parse __HERMES_SESSION_TOKEN__
#      from the HTML response.
#   4. Export HERMES_TUI_GATEWAY_URL + AIFY_HERMES_GATEWAY_URL +
#      AIFY_HERMES_GATEWAY_TOKEN so the Ink TUI attaches via WS instead of
#      spawning its own stdio sidecar, and the aify-comms bridge sees the
#      same gateway.
#   5. Cleanup trap kills the dashboard child on wrapper exit.
#   6. exec hermes chat --tui

cat > "$BIN_DIR/hermes-aify" <<'WRAPPER'
#!/usr/bin/env bash
set -euo pipefail

pick_port() {
  node -e '
    const net = require("net");
    const srv = net.createServer();
    srv.listen(0, "127.0.0.1", () => {
      const p = srv.address().port;
      srv.close(() => { process.stdout.write(String(p)); });
    });
  '
}

wait_for_http() {
  local url="$1"
  local deadline=$(( $(date +%s) + 30 ))
  while [ $(date +%s) -lt "$deadline" ]; do
    if curl -s -o /dev/null "$url"; then return 0; fi
    sleep 0.2
  done
  return 1
}

PORT="$(pick_port)"
if [ -z "$PORT" ]; then
  echo "Failed to allocate a local port for hermes dashboard." >&2
  exit 1
fi
DASHBOARD_URL="http://127.0.0.1:$PORT"

LOG_ROOT="${XDG_STATE_HOME:-$HOME/.local/state}/aify-comms"
mkdir -p "$LOG_ROOT"
LOG_FILE="$LOG_ROOT/hermes-aify-dashboard-$PORT.log"

# Launch the dashboard in the background. --tui sets embedded_chat=True
# which is what mounts /api/ws (web_server.py:3528 etc).
if command -v setsid >/dev/null 2>&1; then
  setsid hermes dashboard --tui --port "$PORT" --no-browser </dev/null >>"$LOG_FILE" 2>&1 &
else
  hermes dashboard --tui --port "$PORT" --no-browser </dev/null >>"$LOG_FILE" 2>&1 &
fi
DASHBOARD_PID=$!

cleanup() {
  if kill -0 "$DASHBOARD_PID" >/dev/null 2>&1; then
    kill "$DASHBOARD_PID" >/dev/null 2>&1 || true
    wait "$DASHBOARD_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if ! wait_for_http "$DASHBOARD_URL/"; then
  echo "hermes-aify could not reach the local dashboard at $DASHBOARD_URL." >&2
  echo "Check $LOG_FILE for details." >&2
  exit 1
fi

# Capture the ephemeral session token from the dashboard's index.html.
# web_server.py:3688 injects: <script>window.__HERMES_SESSION_TOKEN__="..."</script>
TOKEN="$(curl -s "$DASHBOARD_URL/" | grep -oE '__HERMES_SESSION_TOKEN__="[^"]+"' | head -1 | sed -E 's/.*="([^"]+)"$/\1/')"
if [ -z "$TOKEN" ]; then
  echo "hermes-aify could not capture the dashboard session token from $DASHBOARD_URL/." >&2
  exit 1
fi

GATEWAY_URL="ws://127.0.0.1:$PORT/api/ws?token=$TOKEN"
export HERMES_TUI_GATEWAY_URL="$GATEWAY_URL"
export AIFY_HERMES_GATEWAY_URL="$GATEWAY_URL"
export AIFY_HERMES_GATEWAY_TOKEN="$TOKEN"
export AIFY_RUNTIME="hermes"
export AIFY_COMMS_URL="${AIFY_COMMS_URL:-__AIFY_INSTALL_TIME_URL__}"

# Operator-typed session in their real terminal; Ink TUI attaches to the
# dashboard's /api/ws via HERMES_TUI_GATEWAY_URL.
exec hermes chat --tui "$@"
WRAPPER
chmod +x "$BIN_DIR/hermes-aify"
```

(Adjust the actual placement to match install.sh's existing hermes case structure — the above is the body, not the surrounding `case "$cli"` branch.)

- [ ] **Step 3: Syntax check**

Run: `bash -n install.sh && echo "OK"`
Expected: `OK`.

- [ ] **Step 4: Commit**

```bash
git add install.sh
git commit -m "feat(hermes-aify): launch hermes dashboard + capture token + attach chat via HERMES_TUI_GATEWAY_URL"
```

---

## Task 7: Document the resident hermes path

**Files:**
- Modify: `install.hermes.md`
- Modify: `DECISIONS.md`

- [ ] **Step 1: Append a "Resident dispatch delivery" section to install.hermes.md**

Append after the existing "Delivery path" / "Persistent ACP session" sections:

```markdown
## Resident dispatch delivery (operator-launched `hermes-aify`)

`hermes-aify` runs the operator's real Ink terminal TUI for `hermes chat`, AND it accepts bridge-injected aify-comms messages mid-conversation. The mechanism mirrors `codex-aify`:

1. The wrapper spawns `hermes dashboard --tui --port <P> --no-browser` as a hidden background child. This sets `_DASHBOARD_EMBEDDED_CHAT_ENABLED=True` in `hermes_cli/web_server.py`, which mounts the `/api/ws` JSON-RPC endpoint at `tui_gateway/server.py`'s dispatcher.
2. The wrapper fetches `http://127.0.0.1:<P>/` and parses the ephemeral `__HERMES_SESSION_TOKEN__` from the injected script tag.
3. It exports `HERMES_TUI_GATEWAY_URL=ws://127.0.0.1:<P>/api/ws?token=<T>` in the env passed to `hermes chat --tui`. The Ink TUI's `gatewayClient.ts:startAttachedGateway` opens a WebSocket to that URL instead of spawning its own stdio sidecar — operator sees their normal terminal TUI experience.
4. The aify-comms bridge ALSO opens a WebSocket to the same `/api/ws` (it reads `AIFY_HERMES_GATEWAY_URL` from env). For inbound aify-comms messages it sends a JSON-RPC `prompt.submit` (idle session) or `session.steer` (mid-run injection, when `prompt.submit` returns 4009 "session busy"). `tui_gateway/transport.py::TeeTransport` mirrors dispatcher events back to BOTH attached clients, so the operator's TUI renders the injected user turn AND the model's reply naturally.

This is the equivalent of Claude Code's `notifications/claude/channel` delivery and the codex resident `turn/start` delivery — same wrapper-spawned-daemon + transport-pluggable-TUI + bridge-as-second-client shape, no upstream patches required.

**Mid-run insertion (`session.steer`)** is a first-class primitive on the hermes side: text lands on the last tool result of the next tool batch and the model sees it on its next iteration. No interrupt, no role-alternation violation.

**Cleanup:** the wrapper's `trap cleanup EXIT INT TERM` kills the dashboard child on wrapper exit, so `hermes-aify`'s lifecycle owns the dashboard process.
```

- [ ] **Step 2: Append to DECISIONS.md**

```markdown
## Resident hermes uses `hermes dashboard --tui` as a hidden background gateway

**Decision.** `hermes-aify` (install.sh) spawns `hermes dashboard --tui --port <free> --no-browser` as a background child, captures the ephemeral session token from the dashboard's `/` HTML response, then `exec hermes chat --tui` with `HERMES_TUI_GATEWAY_URL=ws://127.0.0.1:<port>/api/ws?token=<token>` in env. The Ink TUI attaches via WebSocket to that gateway instead of spawning its own stdio sidecar. The aify-comms bridge also attaches to the same `/api/ws` and sends `prompt.submit` (idle) / `session.steer` (busy) for inbound aify-comms messages. `TeeTransport` fans out dispatcher events to both attached clients.

**Why.** Symmetric with the codex resident path (`codex-aify` runs `codex app-server` + `codex --remote`). The Ink TUI is already transport-pluggable (`ui-tui/src/gatewayClient.ts:518` reads `HERMES_TUI_GATEWAY_URL`). The dashboard's `/api/ws` is the documented multi-client gateway with the right primitives — `prompt.submit` for new turns, `session.steer` for mid-run insertion. No upstream changes required; everything is available in hermes 0.14+.

**Why not `hermes acp` or `hermes gateway run`.** `hermes acp` is the bridge's managed path and is single-client by design (single `_conn` per session). `hermes gateway run` is for messaging-platform integrations (Telegram/Discord/etc.), not the TUI gateway — name collision was misleading during research.

**Reconsider if.** Upstream ships a dedicated `hermes chat --listen` flag that embeds the WS server in the chat process directly. At that point we drop the dashboard child and use the chat-embedded gateway.
```

- [ ] **Step 3: Commit**

```bash
git add install.hermes.md DECISIONS.md
git commit -m "docs(hermes-resident): document the dashboard-as-gateway + HERMES_TUI_GATEWAY_URL attach path"
```

---

## Task 8: Manual e2e verification on the real hermes CLI

**Files:** None (verification only).

- [ ] **Step 1: Reinstall the wrapper**

Run: `bash install.sh --client hermes http://192.0.2.10:8800 --with-hook` (or whichever invocation matches the operator's setup).
Expected: writes the new `hermes-aify` wrapper to `~/.local/bin/hermes-aify`.

- [ ] **Step 2: Restart any running aify environment bridge so it picks up the rebuilt MCP code**

Operator-specific — restart the host's `aify-comms` environment bridge.

- [ ] **Step 3: Launch a resident hermes agent**

In terminal A: `hermes-aify`
Expected: Ink TUI opens. Background dashboard log lives at `$XDG_STATE_HOME/aify-comms/hermes-aify-dashboard-<port>.log`.

- [ ] **Step 4: Register the hermes agent in aify-comms**

Inside the hermes TUI, invoke `comms_register(agentId="hermes-resident-test", runtime="hermes", sessionMode="resident", cwd="<cwd>")`.
Expected: agent appears in dashboard at `http://localhost:8800`.

- [ ] **Step 5: From another agent, send a message**

`comms_send(from="<other-agent>", to="hermes-resident-test", subject="Wake test", body="Hello hermes from the bridge")`

- [ ] **Step 6: Observe**

- Operator's terminal Ink TUI (terminal A): "Hello hermes from the bridge" should appear as a user turn, hermes responds normally.
- Dashboard Console pane for `hermes-resident-test`: prompt echo + `[hermes] connecting...` + streamed reply.
- Dashboard agent status: flips `available → working → available`.

- [ ] **Step 7: Mid-run test**

Start a long-running query in the operator's TUI (e.g. "explain X in detail"). While the model is mid-stream, from another agent: `comms_send(... body="Stop and answer Y instead")`. Expected: the bridge falls back to `session.steer`, the model sees the steer on its next iteration, output redirects.

- [ ] **Step 8: Record observations + commit if docs need amending**

If the manual run reveals doc gaps, update install.hermes.md / DECISIONS.md and commit. Otherwise no commit on this task.

---

## Task 9: Smoke test docker rebuild + full test suites

- [ ] **Step 1:** `docker compose up -d --build && curl http://127.0.0.1:8800/health` → `{"status":"healthy"}`
- [ ] **Step 2:** Full Node test suite (use the explicit-file invocation from the codex plan's Task 6 since glob doesn't recurse).
- [ ] **Step 3:** Python `pytest service/tests/`.
- [ ] **Step 4:** `bash -n install.sh`.

---

## Self-Review

**Spec coverage:**
- ✅ Symmetric UX with claude/codex → Task 6 (wrapper), Task 5 (bridge), Task 7 (docs)
- ✅ Real Ink TUI visible in terminal → Task 6 spawns `hermes chat --tui` after dashboard ready
- ✅ Mid-run insertion → Task 5 implements `session.steer` fallback when `prompt.submit` returns 4009
- ✅ Hidden background dashboard → Task 6 uses `--no-browser` + setsid; trap cleans up
- ✅ Test coverage → Task 2 (fixture), Task 3 (protocol units), Task 5 (e2e)
- ✅ Flag-name validation upfront → Task 1

**Placeholder scan:** Step 3 of Task 6 (`--no-browser`) is an assumption — Task 1 validates the actual flag name; revise Task 6 inline based on Task 1 output. No other placeholders.

**Type consistency:** `terminalSinkProvider`, `runtimeConfig.gatewayUrl`, `runtimeConfig.gatewayTokenEnv` shape matches the patterns established for codex.

---

## Open Questions

1. Does `hermes dashboard --no-browser` exist exactly as named? Validated in Task 1.
2. Does the dashboard expose `__HERMES_SESSION_TOKEN__` reliably in `/`? Confirmed at `web_server.py:3688` for current hermes 0.14, but Task 1 Step 3 also probes it.
3. Should the bridge cache the resolved `sessionId` per agent across dispatches, or call `session.most_recent` each time? Plan: cache after first resolution; re-resolve on WS reconnect. Cheap optimization that can land later.
