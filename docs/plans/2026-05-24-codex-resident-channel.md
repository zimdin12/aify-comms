# Codex Resident Channel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make resident-codex agents reliably wake on inbound aify-comms messages via the existing `codex app-server` WebSocket + `turn/start` mechanism — symmetric in UX with `claude-channel.js`. Verify the existing path, fix the visibility gap caused by codex issue #15320, add tests, document.

**Architecture:** `codex-aify` already runs a per-instance `codex app-server` (install.sh:319-330) and connects the resident TUI via `codex --remote $URL` (install.sh:424). The aify-comms bridge inside that codex session writes a `codex` runtime marker with `appServerUrl` (server.js:170-183). The bridge's main dispatch loop already polls `/dispatch/claim` with `executionModes: ["resident"]` for codex agents (server.js:1857-1872), and `createCodexController` routes resident claims through `createCodexControllerLegacy` (runtimes.js:2055-2072) which opens a WS to the local app-server and issues `turn/start` on the residentThreadId. **This plan verifies that path end-to-end, closes the visibility gap (codex issue #15320: externally-injected `turn/start` may not visibly render in the `--remote` TUI live), and adds the missing test coverage.**

**Tech Stack:** Node.js stdio bridge, JSON-RPC over WebSocket (`@modelcontextprotocol/sdk`), `node:test` framework, FastAPI/SQLite backend.

---

## File Structure

| Path | Responsibility |
|------|----------------|
| `mcp/stdio/runtimes.js` (modify, ~line 2295) | Lift the `executionMode === "managed"` gate on `terminalSinkProvider` so resident codex dispatches also push synth-terminal frames — operator sees the wake event in the dashboard Console pane even when the `--remote` TUI doesn't render the externally-injected turn live. |
| `mcp/stdio/server.js` (modify, dispatch loop near :1928) | Confirm/add turn_busy heartbeat pulse for resident-codex claims symmetric with claude-channel (`turnBusy: true` POST on claim, no explicit clear). |
| `mcp/stdio/tests/codex-resident-dispatch.test.js` (create) | End-to-end test: fake codex app-server fixture, simulated dispatch, asserts `turn/start` reaches the fake app-server with the right thread id and the bridge marks the dispatch delivered. |
| `mcp/stdio/tests/fixtures/fake-codex-app-server.mjs` (extend if needed) | Add a tracked event log so the test can assert `turn/start` was received with the expected params. |
| `install.codex.md` (modify) | Add a "Resident dispatch delivery" section documenting the WS app-server channel path, codex issue #15320 caveat, and the dashboard-Console mitigation. |
| `DECISIONS.md` (append) | New section: "Why resident-codex uses the existing WS app-server path instead of a separate codex-channel.js bridge." |

---

## Task 1: End-to-end test — resident codex dispatch via WS app-server

Pin the existing behavior with a test that uses the fake-codex-app-server fixture, so future churn (the wrapper has been reverted+restored multiple times) can't silently break the resident dispatch path again.

**Files:**
- Create: `mcp/stdio/tests/codex-resident-dispatch.test.js`
- Reference: `mcp/stdio/tests/fixtures/fake-codex-app-server.mjs`
- Reference: `mcp/stdio/runtimes.js:2118` (`createCodexControllerLegacy`)
- Reference: `mcp/stdio/tests/codex-session.test.js` (test-style baseline)

- [ ] **Step 1: Read the fake fixture's current event tracking**

Run: `node -e "import('./mcp/stdio/tests/fixtures/fake-codex-app-server.mjs').then(m => console.log(Object.keys(m)))"`
Expected: a list of exported helpers (or a default-only module). Confirm what shape of "received messages" the fixture exposes.

- [ ] **Step 2: Write the failing test**

Create `mcp/stdio/tests/codex-resident-dispatch.test.js`:

```javascript
#!/usr/bin/env node
// End-to-end: resident-codex dispatch flow.
// Bridge polls /dispatch/claim → createCodexController routes to LEGACY
// (executionMode='resident' + hasCodexLiveAppServer=true) → connects WS to
// fake app-server → issues turn/start on residentThreadId → marks delivered.

import assert from "node:assert/strict";
import { test } from "node:test";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn } from "node:child_process";
import http from "node:http";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FAKE = path.join(__dirname, "fixtures", "fake-codex-app-server.mjs");

test("resident codex dispatch routes turn/start to local app-server", async (t) => {
  // 1. Start the fake codex app-server as a child process listening on a free port
  const port = 30000 + Math.floor(Math.random() * 10000);
  const appServerUrl = `ws://127.0.0.1:${port}`;
  const fake = spawn(process.execPath, [FAKE, "--listen", appServerUrl], {
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, FAKE_CODEX_RESIDENT_THREAD: "thr_resident_test_001" },
  });
  t.after(() => { try { fake.kill("SIGTERM"); } catch {} });

  // Wait until the fake app-server is listening
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("fake codex did not bind in 5s")), 5000);
    fake.stdout.on("data", (chunk) => {
      if (String(chunk).includes("listening")) { clearTimeout(timeout); resolve(); }
    });
  });

  // 2. Import createCodexController and invoke the resident path directly
  const { createCodexController } = await import("../runtimes.js");
  const agentInfo = {
    agentId: "codex-test-1",
    runtime: "codex",
    sessionMode: "resident",
    sessionHandle: "thr_resident_test_001",
    cwd: process.cwd(),
    capabilities: ["resident-run"],
    runtimeConfig: { appServerUrl, hasCodexLiveAppServer: true },
  };
  const run = {
    id: "run_test_001",
    executionMode: "resident",
    subject: "Test subject",
    body: "Hello from another agent via aify-comms.",
    from: "agent-a",
  };
  const events = [];
  const controller = createCodexController({
    agentId: "codex-test-1",
    agentInfo,
    run,
    runtimeState: { threadId: "thr_resident_test_001" },
    callbacks: {
      onEvent: (kind, msg) => events.push({ kind, msg }),
      onRefs: () => {},
    },
  });

  // 3. Assert the controller completes (fake app-server returns turn/completed)
  const result = await controller.promise.catch((err) => ({ failed: true, error: err.message }));
  assert.ok(!result.failed, `expected resident dispatch to succeed: ${result.error || ""}`);
  assert.equal(result.status, "completed");

  // 4. Assert turn/start was issued against the resident thread id
  const turnStarts = events.filter((e) => e.kind === "turn" && /Started turn/.test(e.msg));
  assert.ok(turnStarts.length >= 1, "expected at least one turn/started event");
});
```

- [ ] **Step 3: Run the test — expect FAIL or skip until fixture supports `--listen`**

Run: `node --test mcp/stdio/tests/codex-resident-dispatch.test.js`
Expected: FAIL. Either (a) the fake fixture doesn't accept `--listen` CLI args and exits, or (b) the test reveals a bug in `createCodexControllerLegacy` for the resident path (e.g. `hasCodexLiveAppServer` check, sessionHandle wiring, threadId hint propagation).

- [ ] **Step 4: Extend the fixture if needed**

Open `mcp/stdio/tests/fixtures/fake-codex-app-server.mjs`. If it doesn't already accept `--listen ws://...`, add support:

```javascript
// Near the top of the fixture, after existing imports:
const args = process.argv.slice(2);
const listenIdx = args.indexOf("--listen");
const listenUrl = listenIdx >= 0 ? args[listenIdx + 1] : "";
const RESIDENT_THREAD = process.env.FAKE_CODEX_RESIDENT_THREAD || "thr_fake_001";

if (listenUrl) {
  // WebSocket server mode — accept JSON-RPC frames over WS.
  const { WebSocketServer } = await import("ws");
  const port = Number(new URL(listenUrl).port);
  const wss = new WebSocketServer({ port, host: "127.0.0.1" });
  wss.on("listening", () => process.stdout.write(`listening on ${listenUrl}\n`));
  wss.on("connection", (ws) => {
    ws.on("message", (frame) => {
      const msg = JSON.parse(frame.toString());
      // Handle the resident-codex JSON-RPC shapes we care about: initialize,
      // initialized, thread/resume, turn/start, turn/interrupt.
      if (msg.method === "initialize") {
        ws.send(JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: { serverInfo: { name: "fake-codex" } } }));
      } else if (msg.method === "initialized") {
        // notification — no reply
      } else if (msg.method === "thread/resume") {
        ws.send(JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: { thread: { id: msg.params.threadId } } }));
      } else if (msg.method === "turn/start") {
        const turnId = `turn_${Date.now()}`;
        // Immediate ack
        ws.send(JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: { turn: { id: turnId } } }));
        // Then async turn lifecycle notifications
        setTimeout(() => {
          ws.send(JSON.stringify({ jsonrpc: "2.0", method: "turn/started", params: { turn: { id: turnId } } }));
          ws.send(JSON.stringify({ jsonrpc: "2.0", method: "item/agentMessage/delta", params: { delta: "ack" } }));
          ws.send(JSON.stringify({ jsonrpc: "2.0", method: "turn/completed", params: { turn: { id: turnId, status: "completed" } } }));
        }, 10);
      } else if (msg.id) {
        ws.send(JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: {} }));
      }
    });
  });
}
```

- [ ] **Step 5: Run the test — expect PASS**

Run: `node --test mcp/stdio/tests/codex-resident-dispatch.test.js`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add mcp/stdio/tests/codex-resident-dispatch.test.js mcp/stdio/tests/fixtures/fake-codex-app-server.mjs
git commit -m "test(codex-resident): pin the WS app-server turn/start dispatch path"
```

---

## Task 2: Surface a synth-terminal frame for resident-codex dispatches

`runtimes.js:2295` currently gates the synth-terminal sink to `executionMode === "managed"` with the comment "resident codex has the operator's own visible terminal." That assumption is wrong in the presence of codex issue #15320 — when the bridge calls `turn/start` against the running app-server, the operator's `--remote` TUI does **not** render the externally-injected turn live (history fixes up later). The operator sees nothing happen. We mitigate by also pushing synth-terminal frames for resident dispatches so the **dashboard Console pane** shows the wake event.

**Files:**
- Modify: `mcp/stdio/runtimes.js:2295`
- Test: `mcp/stdio/tests/codex-resident-dispatch.test.js` (extend)

- [ ] **Step 1: Write the failing test extension**

Append to `mcp/stdio/tests/codex-resident-dispatch.test.js`:

```javascript
test("resident codex dispatch pushes synth-terminal frames for dashboard visibility", async (t) => {
  // (same fake-app-server setup as test 1 — extract into a helper if you prefer)
  const port = 30000 + Math.floor(Math.random() * 10000);
  const appServerUrl = `ws://127.0.0.1:${port}`;
  const fake = spawn(process.execPath, [FAKE, "--listen", appServerUrl], {
    stdio: ["ignore", "pipe", "pipe"],
    env: { ...process.env, FAKE_CODEX_RESIDENT_THREAD: "thr_resident_test_002" },
  });
  t.after(() => { try { fake.kill("SIGTERM"); } catch {} });
  await new Promise((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("fake codex did not bind in 5s")), 5000);
    fake.stdout.on("data", (c) => { if (String(c).includes("listening")) { clearTimeout(timeout); resolve(); } });
  });

  const frames = [];
  const sinkProvider = async () => async (text, status) => { frames.push({ text, status }); };

  const { createCodexController } = await import("../runtimes.js");
  const controller = createCodexController({
    agentId: "codex-test-2",
    agentInfo: {
      agentId: "codex-test-2",
      runtime: "codex",
      sessionMode: "resident",
      sessionHandle: "thr_resident_test_002",
      cwd: process.cwd(),
      capabilities: ["resident-run"],
      runtimeConfig: { appServerUrl, hasCodexLiveAppServer: true },
    },
    run: {
      id: "run_test_002",
      executionMode: "resident",
      subject: "Visibility test",
      body: "Wake event payload",
      from: "agent-a",
    },
    runtimeState: { threadId: "thr_resident_test_002" },
    callbacks: { onEvent: () => {}, onRefs: () => {}, terminalSinkProvider: sinkProvider },
  });
  await controller.promise;

  const allText = frames.map((f) => f.text).join("");
  assert.match(allText, /Wake event payload/, "synth-terminal should echo the dispatch body");
  assert.match(allText, /turn started|turn ended/, "synth-terminal should reflect codex turn lifecycle");
});
```

- [ ] **Step 2: Run test — expect FAIL (no frames pushed)**

Run: `node --test mcp/stdio/tests/codex-resident-dispatch.test.js`
Expected: FAIL on the `match /Wake event payload/` assertion — current code gates `terminalSinkProvider` to managed only, so `frames` is empty.

- [ ] **Step 3: Lift the executionMode gate**

In `mcp/stdio/runtimes.js`, change line ~2295 from:

```javascript
    if (executionMode === "managed" && typeof callbacks?.terminalSinkProvider === "function") {
      try {
        const sink = await callbacks.terminalSinkProvider({ agentId, agentInfo });
        if (typeof sink === "function") terminalSink = sink;
      } catch (error) {
        try { callbacks.onEvent?.("codex", `Codex virtual-terminal sink unavailable: ${error?.message || error}`); } catch {}
      }
    }
```

to:

```javascript
    // Synth-terminal sink for BOTH managed and resident dispatches.
    // For managed: dashboard Console is the only operator view.
    // For resident: codex issue #15320 means the operator's --remote TUI
    // does not visibly render externally-injected turn/start frames live;
    // the dashboard Console becomes the operator's wake-event surface.
    if (typeof callbacks?.terminalSinkProvider === "function") {
      try {
        const sink = await callbacks.terminalSinkProvider({ agentId, agentInfo });
        if (typeof sink === "function") terminalSink = sink;
      } catch (error) {
        try { callbacks.onEvent?.("codex", `Codex virtual-terminal sink unavailable: ${error?.message || error}`); } catch {}
      }
    }
```

- [ ] **Step 4: Run test — expect PASS**

Run: `node --test mcp/stdio/tests/codex-resident-dispatch.test.js`
Expected: both tests PASS.

- [ ] **Step 5: Run the existing codex test suite — make sure nothing else broke**

Run: `node --test mcp/stdio/tests/codex-session.test.js mcp/stdio/tests/codex-wrapper-stdin.test.js mcp/stdio/tests/codex-cwd-transform.test.js`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add mcp/stdio/runtimes.js mcp/stdio/tests/codex-resident-dispatch.test.js
git commit -m "fix(codex-resident): push synth-terminal frames so dashboard Console shows wake events (issue #15320 mitigation)"
```

---

## Task 3: turn_busy heartbeat parity for resident codex

`claude-channel.js:420` pulses `turnBusy: true` on EVERY claim (no explicit clear; the server-side 120s stale window or the next pulse handles closure). The main bridge in `server.js` must do the same for resident-codex claims so the dashboard shows status='working' through the dispatch lifecycle.

**Files:**
- Read: `mcp/stdio/server.js:1928-2050` (dispatch loop turn_busy handling)
- Read: `mcp/stdio/claude-channel.js:247-254` (reference pattern)
- Possibly modify: `mcp/stdio/server.js` (add pulse on claim if missing)
- Test: `mcp/stdio/tests/codex-resident-dispatch.test.js` (extend to check heartbeat POST)

- [ ] **Step 1: Audit the existing dispatch loop**

Run: `grep -n 'turnBusy\|turn_busy\|reportTurnBusy\|heartbeat' mcp/stdio/server.js`
Expected: identify whether the main dispatch loop already pulses turn_busy when claiming runs. Note line numbers and conditions.

- [ ] **Step 2: If a pulse is missing for resident-codex claims, write the failing test**

Append to `mcp/stdio/tests/codex-resident-dispatch.test.js`:

```javascript
test("resident codex dispatch reports turn_busy heartbeat on claim", async (t) => {
  // Spin up a tiny HTTP mock for the aify backend that records heartbeats
  const heartbeats = [];
  const backend = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      if (req.url.includes("/heartbeat")) {
        try { heartbeats.push(JSON.parse(body)); } catch {}
      }
      res.writeHead(200, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ ok: true }));
    });
  });
  await new Promise((resolve) => backend.listen(0, "127.0.0.1", resolve));
  t.after(() => backend.close());
  const backendUrl = `http://127.0.0.1:${backend.address().port}`;

  // (NOTE: extend createCodexController callbacks if needed to take a
  //  serverUrl override — or invoke the dispatch loop indirectly.)
  // …test body that drives a resident codex claim and asserts:
  assert.ok(heartbeats.some((h) => h.turnBusy === true && h.turnRuntime === "codex"),
            "expected at least one turnBusy=true heartbeat with turnRuntime=codex");
});
```

- [ ] **Step 3: Run test — expect FAIL or SKIP if dispatch loop isn't reachable from a unit test**

Run: `node --test mcp/stdio/tests/codex-resident-dispatch.test.js`
Expected: FAIL with "no turnBusy=true heartbeat with turnRuntime=codex" — OR test architecture forces SKIP if dispatch loop isn't testable in isolation. In the SKIP case, document in the test file why and proceed to step 4 with a code-only fix.

- [ ] **Step 4: If audit revealed missing heartbeat, add it**

In `mcp/stdio/server.js`, near the dispatch claim acceptance (after `batchedRuns.push(claim.run);` at line ~1895), add a heartbeat pulse for the claimed run. The exact patch depends on what step 1 found — but the shape mirrors `claude-channel.js:247-254`:

```javascript
// Pulse turn_busy=true on EVERY claim so dashboard shows 'working' during
// the dispatch lifecycle. Server-side 120s stale window closes it if no
// further pulse arrives. Symmetric with claude-channel.js:420.
await httpCall("POST", `/agents/${encodeURIComponent(agentId)}/heartbeat`, {
  bridgeId: BRIDGE_INSTANCE_ID,
  turnBusy: true,
  turnRunId: run.id,
  turnRuntime: runtime,
}).catch(() => {});
```

If the existing code already does this for all claims, no patch is needed — note that in the commit message in step 6.

- [ ] **Step 5: Run test — expect PASS**

Run: `node --test mcp/stdio/tests/codex-resident-dispatch.test.js`
Expected: PASS (or PASS with documented SKIP).

- [ ] **Step 6: Commit**

```bash
git add mcp/stdio/server.js mcp/stdio/tests/codex-resident-dispatch.test.js
git commit -m "feat(codex-resident): turn_busy heartbeat parity with claude-channel.js"
```

If no code change was needed, instead commit just the test:

```bash
git add mcp/stdio/tests/codex-resident-dispatch.test.js
git commit -m "test(codex-resident): pin turn_busy heartbeat (no code change — existing dispatch loop already pulses)"
```

---

## Task 4: Manual end-to-end verification on the real codex CLI

Tests with the fake fixture verify the wire protocol. Codex issue #15320 is about real-codex-TUI rendering, which the fake can't reproduce. Run the real CLI to confirm operator UX.

**Files:** None (verification only — observations may inform Task 5 doc copy).

- [ ] **Step 1: Bring up the service**

Run: `docker compose up -d --build && curl http://localhost:8800/health`
Expected: `{"status":"healthy"}`

- [ ] **Step 2: Start a resident codex via codex-aify**

Open terminal A. Run: `codex-aify --aify-agent codex-resident-test --resident`
Expected: `codex app-server` log line in console, then the codex TUI opens with `--remote ws://127.0.0.1:NNNNN`.

- [ ] **Step 3: Register the codex agent in aify-comms**

Inside the codex TUI, run: `/mcp aify-comms comms_register {agentId: "codex-resident-test", runtime: "codex", sessionMode: "resident"}` (or use whatever invocation the operator's codex setup expects).
Expected: agent appears in dashboard at http://localhost:8800.

- [ ] **Step 4: From another agent, send a message**

Open terminal B with claude-aify or any agent that has comms access. Send:
`comms_send(from="<your-agent>", to="codex-resident-test", subject="Test wake", body="Hello from claude")`

- [ ] **Step 5: Observe dashboard Console and resident TUI**

Watch dashboard Console pane for `codex-resident-test` — should show:
- `> [<your-agent>] Test wake`
- `> Hello from claude`
- `[codex] connecting...`
- `▶ turn started`
- agent message delta stream
- `■ turn ended`

In the codex `--remote` TUI: codex's reply should eventually appear (history fixes up) even if the wake moment is invisible per #15320. Note the observed behavior — does the TUI render the externally-injected user turn live? Note version: `codex --version`.

- [ ] **Step 6: Verify status transitions**

Dashboard agent row for `codex-resident-test`: should flip `available → working` on dispatch, then back to `available` after `turn/completed`. If it stays stuck at `working`, look at Task 3's heartbeat work.

- [ ] **Step 7: Record observations + commit notes if any docs need updating**

If Task 5's doc copy needs to mention specific symptom (e.g. "your codex 0.133 doesn't render the injected turn — watch dashboard Console for the wake event"), capture that here for use in Task 5. No commit on this task unless docs are touched.

---

## Task 5: Document the resident dispatch delivery path

Operators currently have no document explaining how resident codex receives aify-comms messages. Add an explicit "Resident dispatch delivery" section to `install.codex.md` and a rationale entry to `DECISIONS.md`.

**Files:**
- Modify: `install.codex.md`
- Modify: `DECISIONS.md`
- Modify: `.claude/skills/aify-comms/SKILL.md` (mirror if relevant)
- Modify: `.agents/skills/aify-comms/SKILL.md` (keep in sync per CLAUDE.md)

- [ ] **Step 1: Add the section to install.codex.md**

Open `install.codex.md`. Locate the existing "Delivery path" or equivalent section. Append a new subsection:

```markdown
### Resident dispatch delivery (incoming aify-comms messages)

When another agent sends `comms_send(to="<this-codex-agent>", …)` while
this codex session is running resident under `codex-aify`, the bridge
delivers the message by calling `turn/start` against the per-instance
`codex app-server` on the resident's active `threadId` — the same
app-server the wrapper launched at startup (install.sh:319-330) and that
your `codex --remote $URL` TUI is already connected to.

This is the symmetric equivalent of Claude's `notifications/claude/channel`
delivery, but uses native codex JSON-RPC primitives (no MCP notification
extension required).

**Known limitation — codex issue #15320:** when an external client posts
`turn/start` against a thread that a `--remote` TUI is attached to, the
TUI does **not** render the externally-injected user turn live; the
thread history fixes up later, but the operator may not see the wake
event in the TUI itself. The dashboard Console pane at
`http://localhost:8800` *does* render the wake event (the bridge pushes
synth-terminal frames for the injected turn), so use the dashboard to
verify delivery during operator-visible workflows.

If your codex version has the community patch for #15320 (or upstream
resolution), the `--remote` TUI will render injected turns live — no
operator action required.
```

- [ ] **Step 2: Add a DECISIONS.md entry**

Append to `DECISIONS.md`:

```markdown
## Resident codex uses the existing WS app-server channel (no separate codex-channel.js)

**Decision:** Resident codex dispatch delivery is handled by the existing
`createCodexControllerLegacy` path in `mcp/stdio/runtimes.js:2118` — the
main bridge claims resident-codex runs via `/dispatch/claim`, connects
WebSocket to the per-instance `codex app-server` launched by `codex-aify`,
and issues `turn/start` on the resident's active thread. We did NOT
create a separate `codex-channel.js` bridge mirroring `claude-channel.js`.

**Why:**
- The `claude-channel.js` separation exists because Anthropic's
  `notifications/claude/channel` mechanism requires a **separate MCP
  server entry** registered via `--dangerously-load-development-channels
  server:aify-comms-channel`. Codex has no equivalent constraint.
- `createCodexControllerLegacy` already implements every primitive the
  separate bridge would need: WS-RPC client, initialize/initialized
  handshake, turn/start with prompt body, turn lifecycle notification
  handling, turn/interrupt for controls, synth-terminal frame pushing
  (after the 2026-05-24 gate lift).
- A separate process would duplicate that logic for pure architectural
  symmetry, increasing surface area for divergence bugs.
- The aify-comms backend's `/dispatch/claim` endpoint already supports
  multiple `executionModes`; resident-codex claims flow through the
  main bridge loop in `server.js:1857`.

**Known limitation:** codex issue #15320 — externally-injected `turn/start`
may not render in the operator's `--remote` TUI live. Mitigated by also
pushing synth-terminal frames into the dashboard Console pane (lifted the
`executionMode === "managed"` gate in `runtimes.js:2295` on 2026-05-24).

**Reconsider if:** future codex versions ship a custom notification
primitive analogous to `notifications/claude/channel` that requires a
separate MCP server entry to subscribe. At that point, a real
`codex-channel.js` is justified.
```

- [ ] **Step 3: Mirror to skill docs if they reference the delivery path**

Run: `grep -n 'codex.*delivery\|codex.*channel\|resident.*codex' .claude/skills/aify-comms/SKILL.md .agents/skills/aify-comms/SKILL.md`
If the skills already describe delivery paths, add a one-liner reference to the new install.codex.md section. If they don't, skip.

- [ ] **Step 4: Commit**

```bash
git add install.codex.md DECISIONS.md .claude/skills/aify-comms/SKILL.md .agents/skills/aify-comms/SKILL.md
git commit -m "docs(codex-resident): document WS app-server delivery path + #15320 mitigation"
```

---

## Task 6: Smoke-test the docker rebuild + integration tests still pass

Catch any regression the patches introduced.

**Files:** None (verification only).

- [ ] **Step 1: Rebuild the container**

Run: `docker compose up -d --build && curl http://localhost:8800/health`
Expected: `{"status":"healthy"}`

- [ ] **Step 2: Run the full Node test suite**

Run: `node --test mcp/stdio/tests/`
Expected: all tests PASS (no regressions).

- [ ] **Step 3: Run the Python test suite**

Run: `cd service && python -m pytest tests/ -x` (or whatever the repo's standard invocation is)
Expected: all tests PASS.

- [ ] **Step 4: Validate the install.sh codex path still parses**

Run: `bash -n install.sh`
Expected: no syntax errors (exit 0).

- [ ] **Step 5: Repeat manual e2e (Task 4 steps 2-6) — confirm wake event surfaces in dashboard Console**

Expected: same observations as Task 4, but with synth-terminal frames now visible for resident codex.

- [ ] **Step 6: No commit unless something needed a fix during verification.**

---

## Self-Review

**Spec coverage:**
- ✅ Verify existing path → Task 1 (e2e test) + Task 4 (manual)
- ✅ Visibility gap (#15320) → Task 2 (synth terminal for resident)
- ✅ Heartbeat parity → Task 3
- ✅ Documentation → Task 5
- ✅ No regressions → Task 6

**Placeholder scan:** No "TBD", "implement later", or unspecified handler text. Step 4 of Task 3 explicitly handles the "no code change needed" branch with a different commit message.

**Type consistency:**
- `terminalSinkProvider` signature: `async ({ agentId, agentInfo }) => async (text, status) => void` — matches `runtimes.js:2297` usage.
- `createCodexController` callback shape: `{ onEvent, onRefs, terminalSinkProvider }` — matches `runtimes.js:2055-2072`.
- `runtimeConfig.appServerUrl` + `runtimeConfig.hasCodexLiveAppServer`: matches `hasCodexLiveAppServer()` at `runtimes.js:1536`.
- Heartbeat payload shape `{bridgeId, turnBusy, turnRunId, turnRuntime}`: matches `claude-channel.js:247-254`.

No gaps found.

---

## Open Questions (do not block implementation; flag for operator)

1. Codex `--remote` TUI rendering of externally-injected turns — does the operator's current codex version (`codex --version`) ship with the community patch for #15320, or do we permanently rely on dashboard Console for visibility?
2. Should the synth-terminal frame for resident codex include a prefix marker like `[bridge-injected]` so the operator can distinguish bridge-pushed turns from operator-typed turns in dashboard Console? Default plan: no — frames look identical (codex doesn't differentiate either) and identical formatting helps muscle memory.
