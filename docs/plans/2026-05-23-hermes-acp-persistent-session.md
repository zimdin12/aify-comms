# Hermes ACP Persistent Session — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the per-dispatch `hermes chat -q` controller with a long-lived `hermes acp` JSON-RPC stdio session per agent, so managed-hermes dispatches stream in the background and reuse one process across turns — architectural mirror of `PiSession` (`mcp/stdio/pi-session.js`).

**Architecture:** The bridge spawns one `hermes acp` child per `(agentId, machine)` keyed in a `HermesSession` pool. On first dispatch the bridge performs `initialize` + `session/new`; subsequent dispatches reuse the same `sessionId` via `session/prompt`. `session/update` notifications from the agent are translated into synthesized terminal frames and assistant text the same way `formatPiEventAsTerminalFrame` does for OMP events. The bridge implements ACP CLIENT methods (`fs/read_text_file`, `fs/write_text_file`, `terminal/*`, `session/request_permission`, `session/update`). Resident/native hermes still uses the legacy single-shot path; only the **managed** dispatch route gets the persistent session.

**Tech Stack:** Node.js (mcp/stdio bridge), JSON-RPC over stdio, ACP protocol (Hermes 0.x), pure-JS — no new dependencies. Tests use a fake-hermes-acp stdio fixture in the spirit of the existing fake-omp/fake-codex fixtures.

**Why now:** Operator quote (2026-05-22): *"I do not want pseudo terminal input because i might write while other agent sends message in and it gets scrambled. We neeed to be able to send in background like with claude code pseudo terminal."* The current per-turn `hermes chat -q` spawns a fresh process for every dispatch, can't stream incremental output, and can't carry upstream conversation context (`--continue <name>` only works if the session already exists). ACP gives us stdio JSON-RPC streaming + persistent sessionId + native conversation continuity, with no PTY scrambling.

**Non-goals:**
- Touching the resident (operator-typed) hermes path — that still spawns `hermes` interactively under PTY, this plan is managed-only.
- Codex / OpenCode / Pi conversion — Pi already uses persistent RPC, codex/opencode out of scope.
- Migrating `hermes chat -q` callers other than `createHermesController` managed branch.

---

## File Structure

| File | Status | Responsibility |
|------|--------|----------------|
| `mcp/stdio/hermes-session.js` | **new** (~600 lines) | `HermesSession` class + module-level pool keyed by `agentId`. Spawn/handshake/JSON-RPC plumbing, `session/new`/`prompt`/`cancel`/`close`, idle timeout, heal-on-failure, terminal sink (mirror PiSession). |
| `mcp/stdio/hermes-acp-protocol.js` | **new** (~120 lines) | Pure functions: `formatSessionUpdateAsTerminalFrame(update)` (analog of `formatPiEventAsTerminalFrame`), JSON-RPC framing helpers (`encodeRequest`, `parseMessage`), method-name constants. Pulled out so tests can import without spawning. |
| `mcp/stdio/runtimes.js` | modify (~250 lines diff around line 3376) | `createHermesController` splits: `agentInfo.mode === "managed"` → delegate to `HermesSession.runTurn`; else → existing single-shot path. Add `defaultHermesAcpCommand()` helper. |
| `mcp/stdio/tests/hermes-session-acp.test.js` | **new** (~400 lines) | Round-trip test: spawn fake-hermes-acp fixture, complete initialize+session/new+session/prompt, assert synth terminal frames + summary. Includes cancel, reuse-across-turns, idle-timeout, heal-on-spawn-failure, supersession. |
| `mcp/stdio/tests/fixtures/fake-hermes-acp.mjs` | **new** (~250 lines) | Test double that speaks JSON-RPC over stdio: replies to `initialize`, `session/new`, `session/prompt`; emits a scripted sequence of `session/update` notifications. Behavior is controlled via env vars (e.g., `FAKE_HERMES_ACP_SCRIPT=hello-world.json`). |
| `mcp/stdio/tests/hermes-acp-protocol.test.js` | **new** (~120 lines) | Unit tests for the protocol module: `formatSessionUpdateAsTerminalFrame` covers each content variant. |
| `mcp/stdio/package.json` | modify (1 line in `scripts.test`) | Add the two new test files to the npm test chain. |
| `docs/DECISIONS.md` | modify (append section) | Record: why ACP over PTY for managed-hermes; why managed-only; why per-agent pool not per-session. |
| `install.hermes.md` | modify (append "Persistent ACP session" section) | Note `hermes acp` requirement; how to point bridge at it; how to verify pool from dashboard. |
| `.claude/skills/aify-comms/SKILL.md` + `.agents/skills/aify-comms/SKILL.md` | modify (mirror) | Add hermes to the persistent-worker list alongside pi. |
| `.claude/skills/aify-comms-debug/SKILL.md` + `.agents/skills/aify-comms-debug/SKILL.md` | modify (mirror) | Add hermes-ACP troubleshooting entries: stale session, handshake timeout, idle reaper. |

---

## Background — ACP wire format

Hermes implements **Agent Client Protocol** (ACP). Method names use **snake_case** in `acp.meta`, but on the wire ACP follows JSON-RPC 2.0 with method names like `session/new`, `session/prompt`, `session/update`, `fs/read_text_file`, etc. (slash-separated). The Python `acp` library at `C:\Users\dev\AppData\Local\hermes\hermes-agent\venv\Lib\site-packages\acp\meta.py` lists:

- **Agent methods** (bridge → hermes, request/response): `initialize`, `authenticate`, `session/new`, `session/load`, `session/list`, `session/resume`, `session/fork`, `session/prompt`, `session/cancel`, `session/close`, `session/set_mode`, `session/set_model`, `session/set_config_option`.
- **Client methods** (hermes → bridge, request/response except `session/update` which is notification): `fs/read_text_file`, `fs/write_text_file`, `session/request_permission`, `session/update`, `terminal/create`, `terminal/kill`, `terminal/output`, `terminal/release`, `terminal/wait_for_exit`.

**Framing:** ACP stdio uses **newline-delimited JSON**, one JSON-RPC message per line. (Confirm in Phase A — if Hermes diverges and uses LSP-style `Content-Length`, swap the framing in `hermes-acp-protocol.js`; everything else in the plan stands.)

**Initialize handshake (request):**
```json
{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocol_version":1,"client_capabilities":{"fs":{"read_text_file":true,"write_text_file":true},"terminal":true},"client_info":{"name":"aify-comms-bridge","version":"4.0.0"}}}
```

**Initialize response (from server.py:807):**
```json
{"jsonrpc":"2.0","id":1,"result":{"protocol_version":1,"agent_info":{"name":"hermes-agent","version":"..."},"agent_capabilities":{"load_session":true,"prompt_capabilities":{"image":true},"session_capabilities":{"fork":{},"list":{},"resume":{}}},"auth_methods":[...]}}
```

**session/new (request):**
```json
{"jsonrpc":"2.0","id":2,"method":"session/new","params":{"cwd":"C:/path/to/agent/cwd","mcp_servers":[]}}
```

**session/prompt (request):**
```json
{"jsonrpc":"2.0","id":3,"method":"session/prompt","params":{"session_id":"<uuid>","prompt":[{"type":"text","text":"system + user prompt body"}]}}
```

**session/update notification (server → client, no id):**
```json
{"jsonrpc":"2.0","method":"session/update","params":{"session_id":"...","update":{"session_update":"agent_message_chunk","content":{"type":"text","text":"hi"}}}}
```

Content-block variants we must handle in `formatSessionUpdateAsTerminalFrame`:
- `user_message_chunk` — operator/dispatch text being echoed back (rare; we echo via `echoPromptToTerminal` already).
- `agent_message_chunk` — assistant text delta (the main streaming case).
- `agent_thought_chunk` — reasoning/thinking text (dim/italic in synth terminal).
- `tool_call` — tool invocation start (`yellow → name`).
- `tool_call_update` / `tool_call_progress` — tool progress (drop silently or show as `(running)`).
- `plan` / `agent_plan_update` — turn plan (drop unless verbose).
- `available_commands_update` / `current_mode_update` — internal state, drop.

**session/prompt response (final, after the stream ends):**
```json
{"jsonrpc":"2.0","id":3,"result":{"stop_reason":"end_turn"}}
```
Stop reasons: `end_turn`, `refusal`, `cancelled`, `max_turn_requests`, `max_tokens`.

---

## Task 0: Spike — verify ACP framing and capture a real session log

Goal: avoid baking guesses into the implementation. 30 minutes max.

**Files:**
- Create: `docs/plans/notes/2026-05-23-hermes-acp-spike.md`

- [ ] **Step 1: Run hermes acp by hand, send one initialize, capture wire log**

```bash
# In a separate shell on the host:
hermes acp 2> hermes-acp.stderr | tee hermes-acp.stdout &
HERMES_PID=$!

# Then in another shell, push initialize + session/new + session/prompt as
# newline-delimited JSON via:
node -e '
const { spawn } = require("child_process");
const p = spawn("hermes", ["acp"], { stdio: ["pipe","pipe","pipe"] });
p.stdout.on("data", d => process.stdout.write("[OUT] " + d));
p.stderr.on("data", d => process.stderr.write("[ERR] " + d));
const send = obj => p.stdin.write(JSON.stringify(obj) + "\n");
send({jsonrpc:"2.0",id:1,method:"initialize",params:{protocol_version:1,client_capabilities:{fs:{read_text_file:true,write_text_file:true},terminal:true},client_info:{name:"aify-spike",version:"0"}}});
setTimeout(()=>send({jsonrpc:"2.0",id:2,method:"session/new",params:{cwd:process.cwd(),mcp_servers:[]}}),500);
setTimeout(()=>{}, 30000);
'
```

Expected: see `[OUT]` lines containing JSON-RPC responses + a `session/update` stream after a prompt. Capture into `hermes-acp.stdout`.

- [ ] **Step 2: Verify framing is newline-delimited JSON**

Open `hermes-acp.stdout`. Each line should parse as JSON. If lines are prefixed with `Content-Length: N\r\n\r\n` blocks, framing is LSP-style — note that in the spike doc.

- [ ] **Step 3: Confirm method names on the wire**

Grep the captured `session/update` notifications for `session_update` field values. Cross-check vs the list above. Add any new variants to the spike notes.

- [ ] **Step 4: Save the spike doc + commit**

```bash
git add docs/plans/notes/2026-05-23-hermes-acp-spike.md
git commit -m "docs: hermes ACP wire-spike notes — framing, method names, update variants"
```

Outcome of this task **must** be a short list of confirmed facts (framing? method-name casing? unexpected variants?). The plan assumes newline-delimited JSON and slash-separated method names; if the spike contradicts that, adjust Phase B before continuing.

---

## Task 1: Protocol module + unit tests (Phase A)

**Files:**
- Create: `mcp/stdio/hermes-acp-protocol.js`
- Create: `mcp/stdio/tests/hermes-acp-protocol.test.js`
- Modify: `mcp/stdio/package.json` (add the new test to `scripts.test`)

- [ ] **Step 1: Write failing test for `formatSessionUpdateAsTerminalFrame` — agent_message_chunk**

`mcp/stdio/tests/hermes-acp-protocol.test.js`:
```javascript
#!/usr/bin/env node
import assert from "node:assert/strict";
import { formatSessionUpdateAsTerminalFrame, encodeRequest, parseMessage, METHODS } from "../hermes-acp-protocol.js";

// agent_message_chunk → raw text passthrough
{
  const frame = formatSessionUpdateAsTerminalFrame({
    session_update: "agent_message_chunk",
    content: { type: "text", text: "hello world" },
  });
  assert.equal(frame, "hello world");
}

// agent_thought_chunk → dim+italic wrapper, CRLF-suffixed
{
  const frame = formatSessionUpdateAsTerminalFrame({
    session_update: "agent_thought_chunk",
    content: { type: "text", text: "thinking..." },
  });
  assert.match(frame, /thinking\.\.\./);
  assert.ok(frame.includes("\x1b["), "thought chunks must be ANSI-colorized");
}

// tool_call → yellow arrow + tool name + brief input
{
  const frame = formatSessionUpdateAsTerminalFrame({
    session_update: "tool_call",
    tool_call_id: "tc-1",
    title: "read_file",
    kind: "read",
    raw_input: { path: "README.md" },
  });
  assert.match(frame, /read_file/);
  assert.match(frame, /README\.md/);
}

// unknown variant → empty string (graceful)
{
  const frame = formatSessionUpdateAsTerminalFrame({ session_update: "unknown_kind" });
  assert.equal(frame, "");
}

// encodeRequest → newline-delimited JSON-RPC
{
  const wire = encodeRequest(7, METHODS.SESSION_PROMPT, { session_id: "s", prompt: [{ type: "text", text: "hi" }] });
  assert.ok(wire.endsWith("\n"));
  const parsed = JSON.parse(wire.trim());
  assert.equal(parsed.jsonrpc, "2.0");
  assert.equal(parsed.id, 7);
  assert.equal(parsed.method, "session/prompt");
}

// parseMessage handles batched lines + returns array of objects
{
  const buf = '{"jsonrpc":"2.0","id":1,"result":{}}\n{"jsonrpc":"2.0","method":"session/update","params":{"session_id":"s","update":{}}}\n';
  const { messages, remainder } = parseMessage(buf);
  assert.equal(messages.length, 2);
  assert.equal(remainder, "");
  assert.equal(messages[1].method, "session/update");
}

console.log("hermes-acp-protocol.test.js: all assertions passed");
```

- [ ] **Step 2: Run, verify it fails**

```bash
node mcp/stdio/tests/hermes-acp-protocol.test.js
```
Expected: FAIL with `Cannot find module './hermes-acp-protocol.js'`.

- [ ] **Step 3: Implement `mcp/stdio/hermes-acp-protocol.js`**

```javascript
// JSON-RPC framing + session/update → terminal-frame translation for
// hermes acp. Pure functions; no I/O, no spawn. Used by hermes-session.js
// and by tests.

const ANSI = {
  reset: "\x1b[0m",
  bold: "\x1b[1m",
  dim: "\x1b[2m",
  italic: "\x1b[3m",
  red: "\x1b[31m",
  green: "\x1b[32m",
  yellow: "\x1b[33m",
  cyan: "\x1b[36m",
  brightCyan: "\x1b[96m",
};

const MAX_TOOL_INPUT_BRIEF_CHARS = 240;
const MAX_TOOL_RESULT_BRIEF_CHARS = 320;

export const METHODS = Object.freeze({
  INITIALIZE: "initialize",
  AUTHENTICATE: "authenticate",
  SESSION_NEW: "session/new",
  SESSION_PROMPT: "session/prompt",
  SESSION_CANCEL: "session/cancel",
  SESSION_CLOSE: "session/close",
  SESSION_LOAD: "session/load",
  SESSION_LIST: "session/list",
  SESSION_UPDATE: "session/update",
  SESSION_REQUEST_PERMISSION: "session/request_permission",
  FS_READ_TEXT_FILE: "fs/read_text_file",
  FS_WRITE_TEXT_FILE: "fs/write_text_file",
  TERMINAL_CREATE: "terminal/create",
  TERMINAL_KILL: "terminal/kill",
  TERMINAL_OUTPUT: "terminal/output",
  TERMINAL_RELEASE: "terminal/release",
  TERMINAL_WAIT_FOR_EXIT: "terminal/wait_for_exit",
});

export function encodeRequest(id, method, params) {
  return JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n";
}

export function encodeNotification(method, params) {
  return JSON.stringify({ jsonrpc: "2.0", method, params }) + "\n";
}

export function encodeResponse(id, result) {
  return JSON.stringify({ jsonrpc: "2.0", id, result }) + "\n";
}

export function encodeError(id, code, message) {
  return JSON.stringify({ jsonrpc: "2.0", id, error: { code, message } }) + "\n";
}

// Parse newline-delimited JSON-RPC. Returns { messages: [obj], remainder: string }
// where remainder is any partial trailing line that hasn't terminated yet.
export function parseMessage(buffer) {
  const messages = [];
  const lines = buffer.split("\n");
  const remainder = lines.pop() ?? "";
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed) continue;
    try {
      messages.push(JSON.parse(trimmed));
    } catch {
      // dropped — malformed line; the bridge will surface this via onEvent
    }
  }
  return { messages, remainder };
}

function briefJsonInline(value, limit) {
  if (value === undefined || value === null) return "";
  let text;
  if (typeof value === "string") text = value;
  else { try { text = JSON.stringify(value); } catch { text = String(value); } }
  text = text.replace(/\s+/g, " ").trim();
  if (!text) return "";
  return text.length <= limit ? text : `${text.slice(0, Math.max(0, limit - 1))}…`;
}

function colorize(color, text) {
  if (!text) return "";
  return `${color}${text}${ANSI.reset}`;
}

function chunkText(content) {
  if (!content) return "";
  if (typeof content === "string") return content;
  if (typeof content.text === "string") return content.text;
  if (Array.isArray(content)) return content.map((p) => (p && p.text) || "").join("");
  return "";
}

export function formatSessionUpdateAsTerminalFrame(update) {
  if (!update || typeof update !== "object") return "";
  const kind = String(update.session_update || "");
  switch (kind) {
    case "user_message_chunk": {
      // Bridge already echoes prompt; only emit if hermes echoes something
      // unexpected (e.g. a system-injected nudge).
      const text = chunkText(update.content);
      if (!text) return "";
      return colorize(ANSI.dim, text);
    }
    case "agent_message_chunk":
      return chunkText(update.content);
    case "agent_thought_chunk": {
      const text = chunkText(update.content);
      if (!text) return "";
      return colorize(ANSI.dim + ANSI.italic, text);
    }
    case "tool_call": {
      const name = String(update.title || update.kind || "tool");
      const brief = briefJsonInline(update.raw_input ?? update.input, MAX_TOOL_INPUT_BRIEF_CHARS);
      const head = colorize(ANSI.yellow, `→ ${name}`);
      const detail = brief ? colorize(ANSI.dim, ` ${brief}`) : "";
      return `\r\n${head}${detail}\r\n`;
    }
    case "tool_call_update":
    case "tool_call_progress": {
      // We only surface terminal status (success/failure) when the call
      // wraps up. Intermediate progress would be too noisy.
      const status = String(update.status || "");
      if (status !== "completed" && status !== "failed") return "";
      const name = String(update.title || "tool");
      const ok = status === "completed";
      const brief = briefJsonInline(update.raw_output ?? update.output, MAX_TOOL_RESULT_BRIEF_CHARS);
      const marker = ok ? colorize(ANSI.green, `✓ ${name}`) : colorize(ANSI.red, `✗ ${name}`);
      const detail = brief ? colorize(ANSI.dim, ` ${brief}`) : "";
      return `${marker}${detail}\r\n`;
    }
    case "plan":
    case "agent_plan_update":
    case "available_commands_update":
    case "current_mode_update":
      return "";
    default:
      return "";
  }
}

export function summaryFromTranscript(transcript) {
  // Used by the controller to populate dispatch_run.summary on completion.
  // We accumulate plain assistant text (no thoughts, no tool noise) and
  // return the trimmed total. If empty, the caller falls back to "(no output)".
  return String(transcript || "").trim();
}
```

- [ ] **Step 4: Run, verify passes**

```bash
node mcp/stdio/tests/hermes-acp-protocol.test.js
```
Expected: PASS — `hermes-acp-protocol.test.js: all assertions passed`.

- [ ] **Step 5: Wire test into npm test chain**

Edit `mcp/stdio/package.json`: append ` && node tests/hermes-acp-protocol.test.js` to `scripts.test`.

- [ ] **Step 6: Commit**

```bash
git add mcp/stdio/hermes-acp-protocol.js mcp/stdio/tests/hermes-acp-protocol.test.js mcp/stdio/package.json
git commit -m "feat(hermes-acp): protocol module + session_update → terminal-frame translator"
```

---

## Task 2: fake-hermes-acp test fixture (Phase G prep, brought forward)

We bring the fixture forward because every subsequent task needs it.

**Files:**
- Create: `mcp/stdio/tests/fixtures/fake-hermes-acp.mjs`

- [ ] **Step 1: Write the fixture**

```javascript
#!/usr/bin/env node
// Test double for `hermes acp` — speaks newline-delimited JSON-RPC over
// stdio. Behavior is controlled via env vars so individual tests can
// stage different scenarios without forking the fixture per case.
//
//   FAKE_HERMES_ACP_SCRIPT  — one of: "hello", "tool-call", "cancel", "refuse", "crash-on-init"
//                              Default: "hello"
//   FAKE_HERMES_ACP_DELAY_MS — delay between session/update notifications.
//                              Default: 5
//
// Scripts are intentionally small; each test exercises one wire path.

import readline from "node:readline";

const SCRIPT = String(process.env.FAKE_HERMES_ACP_SCRIPT || "hello");
const DELAY_MS = Number(process.env.FAKE_HERMES_ACP_DELAY_MS || 5);

function send(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

let sessionCounter = 0;
const sessions = new Map(); // sessionId → { cancelled: boolean }

async function delay(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function runPromptScript(reqId, sessionId, promptBlocks) {
  const userText = (promptBlocks || [])
    .filter((b) => b && b.type === "text")
    .map((b) => b.text || "")
    .join("");

  if (SCRIPT === "hello") {
    for (const chunk of ["hello", " ", "world", "\n"]) {
      if (sessions.get(sessionId)?.cancelled) break;
      send({
        jsonrpc: "2.0",
        method: "session/update",
        params: { session_id: sessionId, update: { session_update: "agent_message_chunk", content: { type: "text", text: chunk } } },
      });
      await delay(DELAY_MS);
    }
    const stop = sessions.get(sessionId)?.cancelled ? "cancelled" : "end_turn";
    send({ jsonrpc: "2.0", id: reqId, result: { stop_reason: stop } });
    return;
  }

  if (SCRIPT === "tool-call") {
    send({
      jsonrpc: "2.0",
      method: "session/update",
      params: { session_id: sessionId, update: { session_update: "agent_thought_chunk", content: { type: "text", text: "Reading README" } } },
    });
    await delay(DELAY_MS);
    send({
      jsonrpc: "2.0",
      method: "session/update",
      params: { session_id: sessionId, update: { session_update: "tool_call", tool_call_id: "tc-1", title: "read_file", kind: "read", raw_input: { path: "README.md" } } },
    });
    await delay(DELAY_MS);
    send({
      jsonrpc: "2.0",
      method: "session/update",
      params: { session_id: sessionId, update: { session_update: "tool_call_update", tool_call_id: "tc-1", title: "read_file", status: "completed", raw_output: { length: 1234 } } },
    });
    await delay(DELAY_MS);
    send({
      jsonrpc: "2.0",
      method: "session/update",
      params: { session_id: sessionId, update: { session_update: "agent_message_chunk", content: { type: "text", text: "Done." } } },
    });
    send({ jsonrpc: "2.0", id: reqId, result: { stop_reason: "end_turn" } });
    return;
  }

  if (SCRIPT === "refuse") {
    send({ jsonrpc: "2.0", id: reqId, result: { stop_reason: "refusal" } });
    return;
  }

  // Unknown script — end_turn with no chunks
  send({ jsonrpc: "2.0", id: reqId, result: { stop_reason: "end_turn" } });
}

const rl = readline.createInterface({ input: process.stdin });
rl.on("line", async (line) => {
  const trimmed = line.trim();
  if (!trimmed) return;
  let msg;
  try { msg = JSON.parse(trimmed); } catch { return; }

  if (msg.method === "initialize") {
    if (SCRIPT === "crash-on-init") {
      process.exit(1);
    }
    send({
      jsonrpc: "2.0",
      id: msg.id,
      result: {
        protocol_version: 1,
        agent_info: { name: "fake-hermes", version: "0" },
        agent_capabilities: {
          load_session: true,
          prompt_capabilities: { image: false },
          session_capabilities: { fork: {}, list: {}, resume: {} },
        },
        auth_methods: [],
      },
    });
    return;
  }

  if (msg.method === "session/new") {
    sessionCounter += 1;
    const sessionId = `fake-sess-${sessionCounter}`;
    sessions.set(sessionId, { cancelled: false });
    send({ jsonrpc: "2.0", id: msg.id, result: { session_id: sessionId } });
    return;
  }

  if (msg.method === "session/prompt") {
    runPromptScript(msg.id, msg.params?.session_id, msg.params?.prompt).catch((e) => {
      send({ jsonrpc: "2.0", id: msg.id, error: { code: -32000, message: String(e?.message || e) } });
    });
    return;
  }

  if (msg.method === "session/cancel") {
    const s = sessions.get(msg.params?.session_id);
    if (s) s.cancelled = true;
    send({ jsonrpc: "2.0", id: msg.id, result: null });
    return;
  }

  if (msg.method === "session/close") {
    sessions.delete(msg.params?.session_id);
    send({ jsonrpc: "2.0", id: msg.id, result: null });
    return;
  }

  // Unknown method — return method-not-found
  if (typeof msg.id === "number" || typeof msg.id === "string") {
    send({ jsonrpc: "2.0", id: msg.id, error: { code: -32601, message: `method not found: ${msg.method}` } });
  }
});
```

- [ ] **Step 2: Sanity-check the fixture stands up standalone**

```bash
node -e '
const { spawn } = require("child_process");
const p = spawn("node", ["mcp/stdio/tests/fixtures/fake-hermes-acp.mjs"]);
p.stdout.on("data", d => process.stdout.write("[OUT] " + d));
p.stderr.on("data", d => process.stderr.write("[ERR] " + d));
p.stdin.write(JSON.stringify({jsonrpc:"2.0",id:1,method:"initialize",params:{protocol_version:1}}) + "\n");
setTimeout(()=>p.kill(),500);
'
```
Expected: one `[OUT]` line containing a JSON-RPC response with `id:1` and `result.protocol_version`.

- [ ] **Step 3: Commit**

```bash
git add mcp/stdio/tests/fixtures/fake-hermes-acp.mjs
git commit -m "test(hermes-acp): fake-hermes-acp stdio fixture (initialize, session/new/prompt/cancel/close)"
```

---

## Task 3: HermesSession class — spawn + handshake (Phase B)

**Files:**
- Create: `mcp/stdio/hermes-session.js`
- Create: `mcp/stdio/tests/hermes-session-acp.test.js`

- [ ] **Step 1: Write the first failing test — bridge spawns hermes acp, completes initialize+session/new, ensureStarted resolves**

`mcp/stdio/tests/hermes-session-acp.test.js`:
```javascript
#!/usr/bin/env node
import assert from "node:assert/strict";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { HermesSession } from "../hermes-session.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const FAKE = path.join(__dirname, "fixtures", "fake-hermes-acp.mjs");

// Override the default launcher to point at the fake fixture.
process.env.AIFY_HERMES_ACP_COMMAND = `node ${FAKE}`;

async function test_ensureStarted_completes_handshake() {
  const sess = new HermesSession({
    agentId: "test-agent",
    agentInfo: { runtime: "hermes", mode: "managed", cwd: process.cwd(), runtimeConfig: {} },
  });
  try {
    await sess.ensureStarted();
    assert.equal(typeof sess.sessionId, "string");
    assert.ok(sess.sessionId.startsWith("fake-sess-"), `expected fake-sess-* sessionId, got ${sess.sessionId}`);
  } finally {
    await sess.stop();
  }
  console.log("PASS test_ensureStarted_completes_handshake");
}

await test_ensureStarted_completes_handshake();
console.log("hermes-session-acp.test.js (Phase B): all assertions passed");
```

- [ ] **Step 2: Run, verify it fails**

```bash
node mcp/stdio/tests/hermes-session-acp.test.js
```
Expected: FAIL — `Cannot find module '../hermes-session.js'`.

- [ ] **Step 3: Implement the minimal HermesSession**

`mcp/stdio/hermes-session.js` (initial scaffold — turn flows come in Task 4):
```javascript
// Persistent `hermes acp` child per agent. Mirrors PiSession's
// lifecycle: pool keyed by agentId, ensureStarted handshake, idle-timeout
// reaper, heal-on-failure, terminal sink. JSON-RPC framing + session/update
// translation live in hermes-acp-protocol.js.

import { spawn } from "node:child_process";
import { encodeRequest, parseMessage, METHODS, formatSessionUpdateAsTerminalFrame } from "./hermes-acp-protocol.js";
import { terminateProcessTree, getRuntimeConfig, quoteForDisplay } from "./runtimes.js";

const hermesSessionPool = new Map();

const DEFAULT_IDLE_TIMEOUT_MS = 24 * 60 * 60 * 1000;
const STARTUP_TIMEOUT_DEFAULT_MS = 45000;
const PROMPT_TIMEOUT_DEFAULT_MS = 12 * 60 * 60 * 1000;
const MAX_TERMINAL_FRAME_BUFFER_CHARS = 65536;
const MAX_ASSISTANT_CAPTURE_CHARS = 262144;

function defaultHermesAcpLauncher() {
  const raw = String(process.env.AIFY_HERMES_ACP_COMMAND || process.env.HERMES_ACP_COMMAND || "hermes acp").trim();
  const tokens = raw.split(/\s+/).filter(Boolean);
  return { command: tokens[0], args: tokens.slice(1) };
}

function idleTimeoutFor(agentInfo) {
  const cfg = getRuntimeConfig(agentInfo);
  const fromConfig = Number(cfg.hermesIdleTimeoutMs);
  if (Number.isFinite(fromConfig) && fromConfig > 0) return fromConfig;
  const fromEnv = Number(process.env.AIFY_HERMES_IDLE_TIMEOUT_MS);
  if (Number.isFinite(fromEnv) && fromEnv > 0) return fromEnv;
  return DEFAULT_IDLE_TIMEOUT_MS;
}

function startupTimeoutFor(agentInfo) {
  const cfg = getRuntimeConfig(agentInfo);
  const fromConfig = Number(cfg.startupTimeoutMs);
  if (Number.isFinite(fromConfig) && fromConfig > 0) return fromConfig;
  return STARTUP_TIMEOUT_DEFAULT_MS;
}

function promptTimeoutFor(agentInfo) {
  const cfg = getRuntimeConfig(agentInfo);
  const value = Number(cfg.timeoutMs);
  return Number.isFinite(value) && value > 0 ? value : PROMPT_TIMEOUT_DEFAULT_MS;
}

export class HermesSession {
  constructor({ agentId, agentInfo, onPoolEvent = null } = {}) {
    this.agentId = String(agentId || "").trim();
    this.agentInfo = agentInfo || {};
    this.sessionId = "";
    this._state = "idle"; // idle | starting | ready | stopped | failed
    this._proc = null;
    this._readBuffer = "";
    this._pendingResponses = new Map(); // id → { resolve, reject, method, timer }
    this._requestId = 1;
    this._idleTimer = null;
    this._activeTurn = null;
    this._turnQueue = Promise.resolve();
    this._terminalSink = null;
    this._terminalBuffer = [];
    this._terminalBufferChars = 0;
    this._flushing = false;
    this._terminalFlushChain = Promise.resolve();
    this._onPoolEvent = typeof onPoolEvent === "function" ? onPoolEvent : null;
    this._assistantCapture = "";
    this._stderrCapture = "";
  }

  _emit(kind, payload) {
    if (!this._onPoolEvent) return;
    try { this._onPoolEvent(kind, payload); } catch {}
  }

  async ensureStarted() {
    if (this._state === "ready") return;
    if (this._state === "starting") {
      // wait for in-flight start
      await new Promise((r) => setTimeout(r, 20));
      return this.ensureStarted();
    }
    this._state = "starting";

    const launcher = defaultHermesAcpLauncher();
    const cwd = this.agentInfo.cwd || process.cwd();
    this._proc = spawn(launcher.command, launcher.args, {
      cwd,
      env: { ...process.env, AIFY_BRIDGE_DISABLED: "1", AIFY_AGENT_ID: "" },
      stdio: ["pipe", "pipe", "pipe"],
    });

    this._proc.stdout.on("data", (chunk) => this._onStdout(chunk));
    this._proc.stderr.on("data", (chunk) => {
      this._stderrCapture = (this._stderrCapture + chunk.toString()).slice(-32768);
      this._emit("stderr", quoteForDisplay(chunk.toString()).slice(0, 200));
    });
    this._proc.on("exit", (code, signal) => this._onExit(code, signal));
    this._proc.on("error", (err) => this._onSpawnError(err));

    try {
      await Promise.race([
        this._handshake(cwd),
        new Promise((_, rej) => setTimeout(() => rej(new Error(`hermes acp handshake timeout (${startupTimeoutFor(this.agentInfo)}ms). stderr tail: ${this._stderrCapture.slice(-200)}`)), startupTimeoutFor(this.agentInfo))),
      ]);
      this._state = "ready";
      this._armIdleTimer();
    } catch (error) {
      this._state = "failed";
      try { terminateProcessTree(this._proc); } catch {}
      throw error;
    }
  }

  async _handshake(cwd) {
    const initResult = await this._request(METHODS.INITIALIZE, {
      protocol_version: 1,
      client_capabilities: {
        fs: { read_text_file: true, write_text_file: true },
        terminal: true,
      },
      client_info: { name: "aify-comms-bridge", version: "4.0.0" },
    });
    this._emit("initialize", { agent: initResult?.agent_info });

    const newResult = await this._request(METHODS.SESSION_NEW, {
      cwd,
      mcp_servers: [],
    });
    if (!newResult?.session_id) {
      throw new Error("hermes session/new did not return session_id");
    }
    this.sessionId = String(newResult.session_id);
    this._emit("session_new", { sessionId: this.sessionId });
  }

  _onStdout(chunk) {
    this._readBuffer += chunk.toString();
    const { messages, remainder } = parseMessage(this._readBuffer);
    this._readBuffer = remainder;
    for (const msg of messages) this._dispatchInbound(msg);
  }

  _dispatchInbound(msg) {
    if (msg.id !== undefined && (msg.result !== undefined || msg.error !== undefined)) {
      const pending = this._pendingResponses.get(msg.id);
      if (!pending) return;
      this._pendingResponses.delete(msg.id);
      clearTimeout(pending.timer);
      if (msg.error) pending.reject(new Error(`hermes ${pending.method}: ${msg.error.message || msg.error.code}`));
      else pending.resolve(msg.result);
      return;
    }
    if (msg.method === METHODS.SESSION_UPDATE) {
      this._handleSessionUpdate(msg.params?.update);
      return;
    }
    // Client-method requests from hermes (fs/*, terminal/*, session/request_permission)
    if (msg.method && msg.id !== undefined) {
      this._handleClientRequest(msg);
      return;
    }
  }

  _request(method, params, { timeoutMs = 30000 } = {}) {
    return new Promise((resolve, reject) => {
      const id = this._requestId++;
      const timer = setTimeout(() => {
        this._pendingResponses.delete(id);
        reject(new Error(`hermes ${method} timed out after ${timeoutMs}ms`));
      }, timeoutMs);
      this._pendingResponses.set(id, { resolve, reject, method, timer });
      try {
        this._proc.stdin.write(encodeRequest(id, method, params));
      } catch (err) {
        clearTimeout(timer);
        this._pendingResponses.delete(id);
        reject(err);
      }
    });
  }

  _handleSessionUpdate(/* update */) {
    // Filled in by Task 4 (Phase D). No-op for Phase B handshake test.
  }

  _handleClientRequest(/* msg */) {
    // Filled in by Task 5 (Phase D). For Phase B we just decline.
    // Hermes won't call us during initialize+session/new on the happy path.
  }

  _onExit(code, signal) {
    this._state = "stopped";
    for (const [, pending] of this._pendingResponses) {
      clearTimeout(pending.timer);
      pending.reject(new Error(`hermes acp child exited (code=${code} signal=${signal}). stderr: ${this._stderrCapture.slice(-200)}`));
    }
    this._pendingResponses.clear();
    if (this._idleTimer) { clearTimeout(this._idleTimer); this._idleTimer = null; }
  }

  _onSpawnError(err) {
    this._state = "failed";
    this._emit("spawn-error", { message: err?.message || String(err) });
  }

  _armIdleTimer() {
    if (this._idleTimer) clearTimeout(this._idleTimer);
    this._idleTimer = setTimeout(() => {
      this._emit("idle-reap", { agentId: this.agentId });
      this.stop().catch(() => {});
    }, idleTimeoutFor(this.agentInfo));
    if (typeof this._idleTimer.unref === "function") this._idleTimer.unref();
  }

  async stop() {
    if (this._state === "stopped") return;
    if (this._idleTimer) { clearTimeout(this._idleTimer); this._idleTimer = null; }
    if (this.sessionId && this._state === "ready") {
      try { await this._request(METHODS.SESSION_CLOSE, { session_id: this.sessionId }, { timeoutMs: 2000 }); } catch {}
    }
    try { terminateProcessTree(this._proc); } catch {}
    this._state = "stopped";
    hermesSessionPool.delete(this.agentId);
  }
}

export function getOrCreateHermesSession({ agentId, agentInfo, onPoolEvent }) {
  const key = String(agentId || "").trim();
  if (!key) throw new Error("agentId required for HermesSession pool");
  const existing = hermesSessionPool.get(key);
  if (existing && existing._state !== "stopped" && existing._state !== "failed") return existing;
  const sess = new HermesSession({ agentId: key, agentInfo, onPoolEvent });
  hermesSessionPool.set(key, sess);
  return sess;
}

export function _resetHermesSessionPoolForTests() {
  for (const [, sess] of hermesSessionPool) {
    try { sess.stop(); } catch {}
  }
  hermesSessionPool.clear();
}
```

- [ ] **Step 4: Run, verify Phase B test passes**

```bash
node mcp/stdio/tests/hermes-session-acp.test.js
```
Expected: PASS — both `PASS test_ensureStarted_completes_handshake` and the trailing all-passed line.

- [ ] **Step 5: Commit**

```bash
git add mcp/stdio/hermes-session.js mcp/stdio/tests/hermes-session-acp.test.js
git commit -m "feat(hermes-acp): HermesSession scaffold — spawn + initialize + session/new handshake"
```

---

## Task 4: runTurn — session/prompt + session/update streaming (Phase C+D)

**Files:**
- Modify: `mcp/stdio/hermes-session.js`
- Modify: `mcp/stdio/tests/hermes-session-acp.test.js`

- [ ] **Step 1: Add failing test for runTurn end-to-end**

Append to `mcp/stdio/tests/hermes-session-acp.test.js`:
```javascript
async function test_runTurn_streams_and_returns_summary() {
  const frames = [];
  const sess = new HermesSession({
    agentId: "test-agent-2",
    agentInfo: { runtime: "hermes", mode: "managed", cwd: process.cwd(), runtimeConfig: {} },
  });
  sess.attachTerminalSink(async (text /*, status */) => { frames.push(text); });
  try {
    await sess.ensureStarted();
    const result = await sess.runTurn({
      promptText: "Say hello",
      run: { id: "run-1", body: "Say hello", subject: "test", from: "operator" },
    });
    assert.equal(result.status, "completed");
    assert.ok(result.summary.includes("hello"), `summary missing 'hello': ${result.summary}`);
    const joined = frames.join("");
    assert.ok(joined.includes("hello") && joined.includes("world"), `terminal frames missing tokens: ${joined.slice(0,200)}`);
  } finally {
    await sess.stop();
  }
  console.log("PASS test_runTurn_streams_and_returns_summary");
}

await test_runTurn_streams_and_returns_summary();
```

- [ ] **Step 2: Run, verify it fails (runTurn not defined)**

Expected: FAIL — `sess.runTurn is not a function`.

- [ ] **Step 3: Implement `runTurn` + terminal sink plumbing**

Add to `mcp/stdio/hermes-session.js`:
```javascript
  attachTerminalSink(sink) {
    this._terminalSink = typeof sink === "function" ? sink : null;
    if (this._terminalSink && this._terminalBuffer.length > 0) this._flushTerminalBuffer();
  }

  detachTerminalSink() { this._terminalSink = null; }

  _pushTerminalFrame(text, status = "") {
    const body = String(text || "");
    const stat = String(status || "");
    if (!body && !stat) return;
    this._terminalBuffer.push({ text: body, status: stat });
    this._terminalBufferChars += body.length;
    while (this._terminalBufferChars > MAX_TERMINAL_FRAME_BUFFER_CHARS && this._terminalBuffer.length > 1) {
      const removed = this._terminalBuffer.shift();
      this._terminalBufferChars -= removed.text.length;
    }
    this._flushTerminalBuffer();
  }

  _flushTerminalBuffer() {
    if (!this._terminalSink || this._terminalBuffer.length === 0) return;
    if (this._flushing) return;
    this._flushing = true;
    this._terminalFlushChain = (async () => {
      try {
        while (this._terminalSink && this._terminalBuffer.length > 0) {
          const frame = this._terminalBuffer.shift();
          this._terminalBufferChars = Math.max(0, this._terminalBufferChars - frame.text.length);
          try { await this._terminalSink(frame.text, frame.status); } catch {}
        }
      } finally {
        this._flushing = false;
      }
    })();
  }

  async runTurn({ promptText, run }) {
    await this.ensureStarted();
    // Serialize turns per session. Hermes does not allow concurrent
    // session/prompt for the same session_id; queueing prevents the
    // server from rejecting with a busy error.
    return (this._turnQueue = this._turnQueue.then(() => this._runTurnInner({ promptText, run })));
  }

  async _runTurnInner({ promptText, run }) {
    if (this._idleTimer) { clearTimeout(this._idleTimer); this._idleTimer = null; }
    this._assistantCapture = "";
    const turn = { id: run?.id || "", cancelled: false };
    this._activeTurn = turn;

    // Echo prompt header so dashboard Console shows "> [from] body".
    try {
      const body = String(run?.body || "").trim();
      if (body) {
        const subject = String(run?.subject || "").trim();
        const from = String(run?.from || "dashboard").trim() || "dashboard";
        const header = subject ? `\r\n> [${from}] ${subject}\r\n` : `\r\n> [${from}]\r\n`;
        const prefixed = body.split(/\r?\n/).map((l) => `> ${l}`).join("\r\n");
        this._pushTerminalFrame(`${header}${prefixed}\r\n`, "running");
      }
    } catch {}
    this._pushTerminalFrame("[hermes] thinking...\r\n", "running");

    try {
      const result = await this._request(METHODS.SESSION_PROMPT, {
        session_id: this.sessionId,
        prompt: [{ type: "text", text: String(promptText || "") }],
      }, { timeoutMs: promptTimeoutFor(this.agentInfo) });

      const stop = String(result?.stop_reason || "end_turn");
      const summary = this._assistantCapture.trim() || "(no output)";

      if (stop === "cancelled" || turn.cancelled) {
        this._pushTerminalFrame("\r\n[interrupted]\r\n", "running");
        return { status: "cancelled", summary, runtimeState: {}, externalRefs: {} };
      }
      if (stop === "refusal") {
        this._pushTerminalFrame(`\r\n[refusal] ${summary}\r\n`, "failed");
        return { status: "failed", summary: `Hermes refused the turn: ${summary}`, runtimeState: {}, externalRefs: {} };
      }
      this._pushTerminalFrame(`\r\n[${stop}]\r\n`, "running");
      return { status: "completed", summary, runtimeState: {}, externalRefs: {} };
    } catch (error) {
      this._pushTerminalFrame(`\r\n[error] ${error?.message || error}\r\n`, "failed");
      throw error;
    } finally {
      this._activeTurn = null;
      this._armIdleTimer();
    }
  }

  async cancelActiveTurn() {
    const turn = this._activeTurn;
    if (!turn) return;
    turn.cancelled = true;
    try { await this._request(METHODS.SESSION_CANCEL, { session_id: this.sessionId }, { timeoutMs: 5000 }); } catch {}
  }
```

And replace the `_handleSessionUpdate` stub with:
```javascript
  _handleSessionUpdate(update) {
    if (!update) return;
    const kind = String(update.session_update || "");
    if (kind === "agent_message_chunk") {
      const text = (update.content && update.content.text) || "";
      this._assistantCapture = (this._assistantCapture + text).slice(-MAX_ASSISTANT_CAPTURE_CHARS);
    }
    const frame = formatSessionUpdateAsTerminalFrame(update);
    if (frame) this._pushTerminalFrame(frame, "running");
  }
```

- [ ] **Step 4: Run, verify both Phase B and Phase C tests pass**

```bash
node mcp/stdio/tests/hermes-session-acp.test.js
```
Expected: PASS for both tests.

- [ ] **Step 5: Add wire-into-npm-test entry**

Edit `mcp/stdio/package.json`: append ` && node tests/hermes-session-acp.test.js` to `scripts.test`.

- [ ] **Step 6: Commit**

```bash
git add mcp/stdio/hermes-session.js mcp/stdio/tests/hermes-session-acp.test.js mcp/stdio/package.json
git commit -m "feat(hermes-acp): runTurn with session/prompt + session/update streaming"
```

---

## Task 5: Client-method handlers — fs/*, terminal/*, session/request_permission (Phase D)

Hermes may call **back into the bridge** during a turn for filesystem / permission / terminal operations. If we don't handle these, the turn will stall waiting for a response.

**Files:**
- Modify: `mcp/stdio/hermes-session.js`
- Modify: `mcp/stdio/tests/fixtures/fake-hermes-acp.mjs` (add `client-callback` script)
- Modify: `mcp/stdio/tests/hermes-session-acp.test.js`

- [ ] **Step 1: Extend fake fixture with a client-callback scenario**

Append a new branch inside `runPromptScript`:
```javascript
  if (SCRIPT === "client-callback") {
    // Ask the bridge to read a file, then echo its content back as a chunk.
    const reqId = 9001;
    send({
      jsonrpc: "2.0",
      id: reqId,
      method: "fs/read_text_file",
      params: { session_id: sessionId, path: process.env.FAKE_HERMES_ACP_CB_PATH || "nonexistent.txt", line: null, limit: null },
    });
    // For simplicity, the fixture doesn't await the bridge's response;
    // it just emits a chunk and ends. In a real test we use a Promise that
    // resolves when the bridge writes back to stdin — see fixture v2 in the
    // implementation.
    await delay(DELAY_MS * 4);
    send({
      jsonrpc: "2.0",
      method: "session/update",
      params: { session_id: sessionId, update: { session_update: "agent_message_chunk", content: { type: "text", text: "callback-ok" } } },
    });
    send({ jsonrpc: "2.0", id: reqId, result: { stop_reason: "end_turn" } });
    return;
  }
```

And wire a stdin listener that captures the bridge's response to id 9001 and exposes the content via stderr for the test to inspect (or accumulates into a sessions[sessionId].lastCallbackResponse).

- [ ] **Step 2: Write failing test asserting the bridge replies to fs/read_text_file with `{ content }`**

```javascript
async function test_client_callback_fs_read() {
  const tmpPath = path.join(process.cwd(), "tmp-hermes-acp-fixture.txt");
  await import("node:fs/promises").then((fs) => fs.writeFile(tmpPath, "abc-content-xyz"));
  process.env.FAKE_HERMES_ACP_SCRIPT = "client-callback";
  process.env.FAKE_HERMES_ACP_CB_PATH = tmpPath;
  const sess = new HermesSession({
    agentId: "test-agent-cb",
    agentInfo: { runtime: "hermes", mode: "managed", cwd: process.cwd(), runtimeConfig: {} },
  });
  try {
    await sess.ensureStarted();
    const result = await sess.runTurn({ promptText: "go", run: { id: "r" } });
    assert.equal(result.status, "completed");
  } finally {
    await sess.stop();
    await import("node:fs/promises").then((fs) => fs.unlink(tmpPath).catch(() => {}));
    delete process.env.FAKE_HERMES_ACP_SCRIPT;
    delete process.env.FAKE_HERMES_ACP_CB_PATH;
  }
  console.log("PASS test_client_callback_fs_read");
}
```

- [ ] **Step 3: Implement `_handleClientRequest`**

```javascript
  async _handleClientRequest(msg) {
    const id = msg.id;
    const method = String(msg.method || "");
    const respond = (result) => {
      try { this._proc.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, result }) + "\n"); } catch {}
    };
    const respondError = (code, message) => {
      try { this._proc.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, error: { code, message } }) + "\n"); } catch {}
    };
    try {
      if (method === METHODS.FS_READ_TEXT_FILE) {
        const fs = await import("node:fs/promises");
        const filePath = String(msg.params?.path || "");
        const content = await fs.readFile(filePath, "utf-8");
        respond({ content });
        return;
      }
      if (method === METHODS.FS_WRITE_TEXT_FILE) {
        const fs = await import("node:fs/promises");
        const filePath = String(msg.params?.path || "");
        const content = String(msg.params?.content || "");
        await fs.writeFile(filePath, content, "utf-8");
        respond(null);
        return;
      }
      if (method === METHODS.SESSION_REQUEST_PERMISSION) {
        // Managed mode runs YOLO — always approve. (Mirror of `--yolo` flag
        // we pass to `hermes chat -q` today.) If we ever want per-tool
        // gating, this is the hook.
        const options = Array.isArray(msg.params?.options) ? msg.params.options : [];
        const allowOnce = options.find((o) => o && (o.kind === "allow_once" || o.kind === "allow_always"));
        respond({ outcome: { outcome: "selected", option_id: allowOnce?.option_id || "allow" } });
        return;
      }
      if (method === METHODS.TERMINAL_CREATE || method === METHODS.TERMINAL_KILL || method === METHODS.TERMINAL_OUTPUT || method === METHODS.TERMINAL_RELEASE || method === METHODS.TERMINAL_WAIT_FOR_EXIT) {
        // Hermes-side `terminal/*` lets the agent spawn child processes.
        // We don't have an operator-safe sandbox in the bridge yet, so we
        // decline. The agent will fall back to its own sandbox if it has
        // one, or surface the limitation to the user.
        respondError(-32601, `${method}: bridge does not host hermes child terminals; configure hermes to use its own sandbox.`);
        return;
      }
      respondError(-32601, `unknown client method: ${method}`);
    } catch (error) {
      respondError(-32000, error?.message || String(error));
    }
  }
```

- [ ] **Step 4: Run, verify all three tests pass**

```bash
node mcp/stdio/tests/hermes-session-acp.test.js
```
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp/stdio/hermes-session.js mcp/stdio/tests/fixtures/fake-hermes-acp.mjs mcp/stdio/tests/hermes-session-acp.test.js
git commit -m "feat(hermes-acp): client-method handlers (fs/*, session/request_permission, terminal/* decline)"
```

---

## Task 6: Integration — wire HermesSession into createHermesController (Phase F)

**Files:**
- Modify: `mcp/stdio/runtimes.js` (createHermesController, ~line 3376)

- [ ] **Step 1: Refactor `createHermesController` to split managed vs other**

Replace the body of `createHermesController` (currently lines 3376–3558) with:

```javascript
function createHermesController({ agentId, agentInfo, run, runtimeState, callbacks }) {
  const mode = String(agentInfo.mode || "").toLowerCase();
  if (mode === "managed") {
    return createManagedHermesController({ agentId, agentInfo, run, runtimeState, callbacks });
  }
  return createSingleShotHermesController({ agentId, agentInfo, run, runtimeState, callbacks });
}

async function createManagedHermesController({ agentId, agentInfo, run, runtimeState, callbacks }) {
  const { getOrCreateHermesSession } = await import("./hermes-session.js");
  const sess = getOrCreateHermesSession({
    agentId,
    agentInfo,
    onPoolEvent: (kind, payload) => {
      try { callbacks?.onEvent?.("hermes", `${kind}: ${JSON.stringify(payload).slice(0, 160)}`); } catch {}
    },
  });

  // Terminal sink hookup — provider gets the synth terminal_session row
  // and returns a sink function. Mirror of how pi-session is wired.
  if (typeof callbacks?.terminalSinkProvider === "function") {
    try {
      const sink = await callbacks.terminalSinkProvider({ agentId, agentInfo });
      if (typeof sink === "function") sess.attachTerminalSink(sink);
    } catch (error) {
      try { callbacks.onEvent?.("hermes", `Hermes virtual-terminal sink unavailable: ${error?.message || error}`); } catch {}
    }
  }

  const systemPrompt = buildSystemPrompt(agentId, agentInfo, run);
  const userPrompt = buildUserPrompt(run);
  const fullPrompt = `${systemPrompt}\n\n${userPrompt}`;

  const promise = sess.runTurn({ promptText: fullPrompt, run });

  return {
    capabilities: controlCapabilitiesForRuntime("hermes"),
    interrupt: async () => { await sess.cancelActiveTurn(); },
    steer: async () => {
      throw new Error("Hermes managed runs do not support mid-turn steer (use a follow-up dispatch).");
    },
    promise,
  };
}

function createSingleShotHermesController({ agentId, agentInfo, run, runtimeState, callbacks }) {
  // EXISTING implementation — copy the previous createHermesController
  // body verbatim here (the `hermes chat -q` single-shot path).
  // ...
}
```

(For the single-shot path, paste the original implementation verbatim — that's the code currently between lines 3392 and 3557.)

- [ ] **Step 2: Note — `launchRuntimeRun` returns the controller synchronously**

The existing dispatch loop expects `launchRuntimeRun` to return synchronously. `createManagedHermesController` is now async because of the dynamic `import()`. Two options:

(a) Top-level static `import` of `hermes-session.js` at the top of `runtimes.js`.
(b) Keep dynamic import but wrap the entire managed-path body in a sync constructor that returns `{ promise: (async()=>{ const m=await import(...); ... })() }`.

Pick (a) — top-level import. It's simpler, hermes-session.js has no startup side effects.

Replace the dynamic import with:
```javascript
// At top of runtimes.js, near the other imports:
import { getOrCreateHermesSession } from "./hermes-session.js";
```
And remove `const { getOrCreateHermesSession } = await import("./hermes-session.js");` inside the function.

Make `createManagedHermesController` non-async. The terminalSinkProvider call returns a Promise, so wrap inside `promise`:
```javascript
function createManagedHermesController({ agentId, agentInfo, run, runtimeState, callbacks }) {
  const sess = getOrCreateHermesSession({ agentId, agentInfo, onPoolEvent: (kind, payload) => {
    try { callbacks?.onEvent?.("hermes", `${kind}: ${JSON.stringify(payload).slice(0, 160)}`); } catch {}
  }});

  const promise = (async () => {
    if (typeof callbacks?.terminalSinkProvider === "function") {
      try {
        const sink = await callbacks.terminalSinkProvider({ agentId, agentInfo });
        if (typeof sink === "function") sess.attachTerminalSink(sink);
      } catch (error) {
        try { callbacks.onEvent?.("hermes", `Hermes virtual-terminal sink unavailable: ${error?.message || error}`); } catch {}
      }
    }
    const systemPrompt = buildSystemPrompt(agentId, agentInfo, run);
    const userPrompt = buildUserPrompt(run);
    return sess.runTurn({ promptText: `${systemPrompt}\n\n${userPrompt}`, run });
  })();

  return {
    capabilities: controlCapabilitiesForRuntime("hermes"),
    interrupt: async () => { await sess.cancelActiveTurn(); },
    steer: async () => { throw new Error("Hermes managed runs do not support mid-turn steer (use a follow-up dispatch)."); },
    promise,
  };
}
```

- [ ] **Step 3: Run the full bridge test suite**

```bash
cd mcp/stdio && npm test
```
Expected: all tests pass (including the new hermes-session and protocol tests + the existing hermes-runtime.test.js).

- [ ] **Step 4: Manual live smoke test (optional but recommended)**

```bash
# In a separate session:
docker compose up -d --build
# Register a managed hermes agent via the dashboard, send a comms_dispatch
# to it from a registered claude agent, verify:
#   - Console shows "[hermes] thinking..." then streaming text
#   - Dashboard status goes available → working → available
#   - One `hermes acp` process exists on the host; survives a second dispatch
```

- [ ] **Step 5: Commit**

```bash
git add mcp/stdio/runtimes.js
git commit -m "feat(hermes-acp): wire managed hermes dispatches through HermesSession persistent ACP"
```

---

## Task 7: Reuse + idle-timeout + heal tests (Phase E)

**Files:**
- Modify: `mcp/stdio/tests/hermes-session-acp.test.js`

- [ ] **Step 1: Add reuse-across-turns test**

```javascript
async function test_session_reused_across_turns() {
  const sess = new HermesSession({ agentId: "test-reuse", agentInfo: { runtime: "hermes", mode: "managed", cwd: process.cwd(), runtimeConfig: {} } });
  try {
    await sess.ensureStarted();
    const firstSessionId = sess.sessionId;
    const r1 = await sess.runTurn({ promptText: "hi", run: { id: "r1" } });
    const r2 = await sess.runTurn({ promptText: "again", run: { id: "r2" } });
    assert.equal(r1.status, "completed");
    assert.equal(r2.status, "completed");
    assert.equal(sess.sessionId, firstSessionId, "sessionId must persist across turns");
  } finally { await sess.stop(); }
  console.log("PASS test_session_reused_across_turns");
}
```

- [ ] **Step 2: Add idle-timeout reaper test**

```javascript
async function test_idle_timeout_reaps_session() {
  process.env.AIFY_HERMES_IDLE_TIMEOUT_MS = "100";
  const sess = new HermesSession({ agentId: "test-idle", agentInfo: { runtime: "hermes", mode: "managed", cwd: process.cwd(), runtimeConfig: {} } });
  try {
    await sess.ensureStarted();
    await sess.runTurn({ promptText: "x", run: { id: "r" } });
    await new Promise((r) => setTimeout(r, 300));
    assert.equal(sess._state, "stopped", `expected stopped after idle, got ${sess._state}`);
  } finally {
    delete process.env.AIFY_HERMES_IDLE_TIMEOUT_MS;
  }
  console.log("PASS test_idle_timeout_reaps_session");
}
```

- [ ] **Step 3: Add spawn-failure heal test**

```javascript
async function test_handshake_failure_marks_failed() {
  process.env.FAKE_HERMES_ACP_SCRIPT = "crash-on-init";
  const sess = new HermesSession({ agentId: "test-crash", agentInfo: { runtime: "hermes", mode: "managed", cwd: process.cwd(), runtimeConfig: {} } });
  try {
    await sess.ensureStarted();
    assert.fail("expected ensureStarted to reject");
  } catch (e) {
    assert.ok(/timeout|exited/i.test(e.message || ""), `unexpected error: ${e.message}`);
    assert.equal(sess._state, "failed");
  } finally {
    delete process.env.FAKE_HERMES_ACP_SCRIPT;
  }
  console.log("PASS test_handshake_failure_marks_failed");
}
```

- [ ] **Step 4: Run, verify**

```bash
node mcp/stdio/tests/hermes-session-acp.test.js
```
Expected: all five PASS lines.

- [ ] **Step 5: Commit**

```bash
git add mcp/stdio/tests/hermes-session-acp.test.js
git commit -m "test(hermes-acp): reuse-across-turns, idle-timeout reaper, handshake-failure heal"
```

---

## Task 8: NATIVE_MANAGED + VIRTUAL_RPC sync — confirm hermes already in both sets (defensive)

These were updated in the prior session (commits `0c5ef9a` for NATIVE_MANAGED, `2d230eb` for VIRTUAL_RPC). Confirm here so we don't ship a regression.

**Files:**
- Verify only — no edits expected. If a diff appears, that's a regression and we fix it.

- [ ] **Step 1: Run the sync tests**

```bash
node mcp/stdio/tests/native-managed-sync.test.js
node mcp/stdio/tests/virtual-rpc-runtimes-sync.test.js
```
Expected: both pass.

- [ ] **Step 2: Grep for hermes in both sets**

```bash
node -e 'const t=require("fs").readFileSync("mcp/stdio/dispatch-execution.js","utf-8"); console.log(t.match(/NATIVE_MANAGED_RUNTIMES[^]*?\]/)[0])'
node -e 'const t=require("fs").readFileSync("service/routers/api_v2.py","utf-8"); console.log(t.match(/VIRTUAL_RPC_COMMANDS_BY_RUNTIME[^}]*?\}/)[0])'
```
Expected: both outputs contain `hermes`.

No commit needed unless a discrepancy is found.

---

## Task 9: Documentation (Phase H)

**Files:**
- Modify: `docs/DECISIONS.md` (append)
- Modify: `install.hermes.md` (append section)
- Modify: `.claude/skills/aify-comms/SKILL.md` and `.agents/skills/aify-comms/SKILL.md` (mirror)
- Modify: `.claude/skills/aify-comms-debug/SKILL.md` and `.agents/skills/aify-comms-debug/SKILL.md` (mirror)

- [ ] **Step 1: DECISIONS.md — new entry**

Append:
```markdown
## Hermes managed dispatch uses persistent `hermes acp` JSON-RPC (2026-05-23)

**Decision:** Managed hermes dispatches go through a long-lived `hermes acp` child per agent (see `mcp/stdio/hermes-session.js`), mirroring the `PiSession` pattern. Resident/operator-typed hermes still spawns interactive `hermes` under PTY.

**Why:** Operator constraint — no PTY-input mode for managed dispatches (operator typing + bridge typing races scramble each other). ACP is JSON-RPC stdio with a persistent sessionId, so we can stream `session/update` notifications without sharing a PTY with the operator. The prior `hermes chat -q` per-turn spawn could not stream, could not carry upstream session context, and had to embed all conversation context in the wire prompt every turn.

**Why per-agent pool (not per-session, not per-machine):** An `agentId` is the unit the dispatcher knows about. Multiple sessions sharing one child would require routing notifications by `session_id`, which adds bug surface for negligible memory savings.

**Why we decline `terminal/*` client requests:** The bridge has no operator-safe sandbox for hermes-child processes. Hermes falls back to its own sandbox if one is configured. Re-evaluate when there's an operator demand.
```

- [ ] **Step 2: install.hermes.md — new section**

Append:
```markdown
## Persistent ACP session (managed dispatches)

When you register a hermes agent in **managed** mode, the bridge spawns a long-lived `hermes acp` child once per agent and reuses it across dispatches. This keeps conversation context coherent and lets the dashboard Console stream tokens in real time.

- Default launcher: `hermes acp` (looked up on PATH).
- Override: `AIFY_HERMES_ACP_COMMAND=/abs/path/to/hermes acp` (or via `runtimeConfig.hermesAcpCommand` on the agent record).
- Idle reaper: 24h by default. Override per-agent via `runtimeConfig.hermesIdleTimeoutMs` or globally via `AIFY_HERMES_IDLE_TIMEOUT_MS`.
- Resident hermes is unaffected — the operator-typed hermes still launches interactively under PTY.

To verify the persistent child is alive: on the dashboard, open the agent's Console after a dispatch; status should go `available → working → available` while the same `hermes acp` PID stays up between turns (`ps -ef | grep "hermes acp"` on the host).
```

- [ ] **Step 3: SKILL.md mirrors — persistent-worker list**

In both `.claude/skills/aify-comms/SKILL.md` and `.agents/skills/aify-comms/SKILL.md`, find the persistent-worker section (currently lists pi) and add hermes:
```markdown
- **pi (managed):** persistent `omp --mode rpc` per agent; conversation continuity native.
- **hermes (managed):** persistent `hermes acp` JSON-RPC per agent; conversation continuity native (since 2026-05-23).
```

- [ ] **Step 4: Debug SKILL.md mirrors — known issues**

Add entries to both `.claude/skills/aify-comms-debug/SKILL.md` and `.agents/skills/aify-comms-debug/SKILL.md`:
```markdown
### Hermes ACP handshake timeout

Symptom: dispatch fails with `hermes acp handshake timeout`. Cause: `hermes acp` not on PATH, wrong version, or another process owns the binary.

Fix: set `AIFY_HERMES_ACP_COMMAND` to an absolute path; restart `claude-aify` / `codex-aify` wrappers to reload the env.

### Stale hermes session (turn hangs forever)

Symptom: managed hermes dispatch sits at `[hermes] thinking...` and never returns. `ps` shows `hermes acp` is alive.

Fix: in the dashboard, kill the agent's persistent worker (terminal Stop), then re-dispatch — bridge will spawn a fresh `hermes acp` and `session/new`.
```

- [ ] **Step 5: Commit docs**

```bash
git add docs/DECISIONS.md install.hermes.md .claude/skills/aify-comms/SKILL.md .agents/skills/aify-comms/SKILL.md .claude/skills/aify-comms-debug/SKILL.md .agents/skills/aify-comms-debug/SKILL.md
git commit -m "docs(hermes-acp): DECISIONS + install + skills (managed + debug) for persistent ACP session"
```

---

## Task 10: End-to-end live verification + branch handoff

- [ ] **Step 1: Full test suite green**

```bash
docker compose up -d --build
curl http://localhost:8800/health
cd mcp/stdio && npm test && cd ../..
python -m pytest service/tests/
```
Expected: all green.

- [ ] **Step 2: Live two-agent round-trip**

- Register `manager` (claude-code resident) and `worker` (hermes managed) via the dashboard.
- From `manager`: `comms_send to=worker subject="ping" body="reply with the word pong"`.
- Verify in dashboard:
  - `worker` status: available → working → available.
  - `worker` Console: streams "pong" token-by-token.
  - `manager` inbox: receives the reply within seconds.
  - `ps -ef | grep "hermes acp"` on host: one PID, stays alive after the turn completes.
- Send a second dispatch immediately. Verify the same PID handles it (no new spawn).

- [ ] **Step 3: Push branch + open PR**

```bash
git push -u origin feature/dashboard-console-mode
gh pr create --title "feat(hermes-acp): persistent hermes acp session for managed dispatches" --body "$(cat <<'EOF'
## Summary
- Replace per-turn \`hermes chat -q\` for managed hermes with a long-lived \`hermes acp\` JSON-RPC stdio session per agent — mirror of PiSession.
- Streams \`session/update\` notifications into the dashboard Console; reuses one process across turns; carries native conversation context.
- Resident hermes path unchanged.

## Test plan
- [x] \`mcp/stdio && npm test\` (incl. new hermes-acp-protocol + hermes-session-acp tests)
- [x] \`pytest service/tests/\`
- [x] Live two-agent round-trip: dispatch from claude → hermes managed, verify streaming + reuse PID across two consecutive dispatches

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-review checklist

**Spec coverage:** Every requirement maps to a task — protocol module (T1), fixture (T2), spawn+handshake (T3), runTurn+streaming (T4), client callbacks (T5), integration (T6), reuse/heal/idle (T7), set sync (T8), docs (T9), live verify (T10).

**Placeholder scan:** None — every code block is concrete. The one "EXISTING implementation — copy the previous … body verbatim here" in Task 6 is explicit copy-paste, not a placeholder.

**Type consistency:** `getOrCreateHermesSession`, `HermesSession.ensureStarted`, `HermesSession.runTurn`, `HermesSession.cancelActiveTurn`, `HermesSession.attachTerminalSink`/`detachTerminalSink`, `HermesSession.stop` — used consistently across T3/T4/T5/T6. Method strings (`session/new`, `session/prompt`, etc.) come from the `METHODS` constant in `hermes-acp-protocol.js`.

**Operator constraints preserved:**
- No PTY input — managed path uses JSON-RPC stdio only (no node-pty).
- Background delivery — streaming via `session/update` notifications, sink writes into synth terminal, dashboard Console reads from `terminal_session.output` (existing path).
- Resident hermes untouched.
- Conversation continuity native via persistent `session_id` (no more `--continue <name>` workaround).

---

## Execution Handoff

**Plan complete and saved to `docs/plans/2026-05-23-hermes-acp-persistent-session.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.

**2. Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

**Which approach?**
