#!/usr/bin/env node
// Test double for `codex app-server` — speaks newline-delimited JSON-RPC
// over stdio (default) OR over WebSocket (when invoked with --listen).
//
// Stdio mode is what CodexSession (managed pool) uses.
// WebSocket mode is what createCodexControllerLegacy (resident dispatch)
// uses; it mimics the local `codex app-server --listen ws://...` the
// codex-aify wrapper spawns at install.sh:319-330.
//
// Scripts (set via FAKE_CODEX_SCRIPT env):
//   - hello (default)      : emit "hello world\n" as agentMessage deltas, turn/completed status=completed
//   - tool-call            : emit item/started("local_shell_call") → item/completed → agentMessage delta → completed
//   - interrupt            : emit one delta, then wait for turn/interrupt; reply turn/completed status=interrupted
//   - refuse               : turn/completed status=failed
//   - crash-on-init        : exit(1) on initialize
//   - quiet                : no deltas, never emit turn/completed (lets quiet-timeout fire)

import readline from "node:readline";

const SCRIPT = String(process.env.FAKE_CODEX_SCRIPT || "hello");
const DELAY_MS = Number(process.env.FAKE_CODEX_DELAY_MS || 5);

const cliArgs = process.argv.slice(2);
const listenIdx = cliArgs.indexOf("--listen");
const LISTEN_URL = listenIdx >= 0 ? String(cliArgs[listenIdx + 1] || "") : "";
const RESIDENT_THREAD = String(process.env.FAKE_CODEX_RESIDENT_THREAD || "");

let threadCounter = 0;
let turnCounter = 0;
const threads = new Map(); // threadId → { interrupted, activeTurnId }
let interruptResolver = null;
if (RESIDENT_THREAD) threads.set(RESIDENT_THREAD, { interrupted: false, activeTurnId: null });

// Pluggable sender: stdio writes JSON to stdout; WS writes to the open socket.
let sendFn = (obj) => process.stdout.write(JSON.stringify(obj) + "\n");
function send(obj) { sendFn(obj); }
function delay(ms) { return new Promise((r) => setTimeout(r, ms)); }

async function runTurn(reqId, threadId, _params) {
  const turn = `turn-${++turnCounter}`;
  const t = threads.get(threadId);
  if (t) t.activeTurnId = turn;
  send({ jsonrpc: "2.0", id: reqId, result: { turn: { id: turn } } });

  send({ jsonrpc: "2.0", method: "turn/started", params: { turn: { id: turn } } });
  await delay(DELAY_MS);

  if (SCRIPT === "hello") {
    for (const chunk of ["hello", " ", "world", "\n"]) {
      send({ jsonrpc: "2.0", method: "item/agentMessage/delta", params: { delta: chunk } });
      await delay(DELAY_MS);
    }
    send({ jsonrpc: "2.0", method: "turn/completed", params: { turn: { id: turn, status: "completed", usage: { input_tokens: 7, output_tokens: 3 } } } });
    return;
  }

  if (SCRIPT === "tool-call") {
    send({ jsonrpc: "2.0", method: "item/started", params: { item: { id: "i1", type: "local_shell_call" } } });
    await delay(DELAY_MS);
    send({ jsonrpc: "2.0", method: "item/completed", params: { item: { id: "i1", type: "local_shell_call" } } });
    await delay(DELAY_MS);
    send({ jsonrpc: "2.0", method: "item/agentMessage/delta", params: { delta: "done" } });
    send({ jsonrpc: "2.0", method: "turn/completed", params: { turn: { id: turn, status: "completed" } } });
    return;
  }

  if (SCRIPT === "interrupt") {
    send({ jsonrpc: "2.0", method: "item/agentMessage/delta", params: { delta: "thinking..." } });
    // Wait for turn/interrupt to arrive on stdin.
    await new Promise((r) => { interruptResolver = r; setTimeout(r, 5000); });
    send({ jsonrpc: "2.0", method: "turn/completed", params: { turn: { id: turn, status: "interrupted" } } });
    return;
  }

  if (SCRIPT === "refuse") {
    send({ jsonrpc: "2.0", method: "turn/completed", params: { turn: { id: turn, status: "failed", error: { message: "policy denied" } } } });
    return;
  }

  if (SCRIPT === "quiet") {
    // never complete
    return;
  }

  send({ jsonrpc: "2.0", method: "turn/completed", params: { turn: { id: turn, status: "completed" } } });
}

async function handleMessage(msg) {
  if (msg.method === "initialize") {
    if (SCRIPT === "crash-on-init") process.exit(1);
    send({ jsonrpc: "2.0", id: msg.id, result: {} });
    return;
  }
  if (msg.method === "initialized") return; // notification

  if (msg.method === "thread/start") {
    threadCounter += 1;
    const threadId = `fake-thread-${threadCounter}`;
    threads.set(threadId, { interrupted: false, activeTurnId: null });
    send({ jsonrpc: "2.0", id: msg.id, result: { thread: { id: threadId } } });
    return;
  }

  if (msg.method === "thread/resume") {
    const tid = String(msg.params?.threadId || "");
    if (!tid || tid.startsWith("bad-")) {
      send({ jsonrpc: "2.0", id: msg.id, error: { code: -32000, message: "rollout not found" } });
      return;
    }
    threads.set(tid, { interrupted: false, activeTurnId: null });
    send({ jsonrpc: "2.0", id: msg.id, result: { thread: { id: tid } } });
    return;
  }

  if (msg.method === "thread/list") {
    const threadItems = Array.from(threads.keys()).map((id, index) => ({
      id,
      sessionId: id,
      cwd: process.cwd(),
      updatedAt: Date.now() + index,
      status: { type: "idle" },
    }));
    const key = process.env.FAKE_CODEX_THREAD_LIST_KEY === "threads" ? "threads" : "data";
    send({ jsonrpc: "2.0", id: msg.id, result: { [key]: threadItems } });
    return;
  }

  if (msg.method === "turn/start") {
    runTurn(msg.id, msg.params?.threadId, msg.params).catch((e) => {
      send({ jsonrpc: "2.0", id: msg.id, error: { code: -32000, message: String(e?.message || e) } });
    });
    return;
  }

  if (msg.method === "turn/interrupt") {
    if (interruptResolver) { interruptResolver(); interruptResolver = null; }
    send({ jsonrpc: "2.0", id: msg.id, result: {} });
    return;
  }

  if (msg.id !== undefined) {
    send({ jsonrpc: "2.0", id: msg.id, error: { code: -32601, message: `method not found: ${msg.method}` } });
  }
}

if (LISTEN_URL) {
  // WebSocket mode — resident-codex dispatch path. Bridge connects via
  // createWebSocketRpcClient(appServerUrl) from runtimes.js:1679.
  const { WebSocketServer } = await import("ws");
  const url = new URL(LISTEN_URL);
  const port = Number(url.port);
  const host = url.hostname || "127.0.0.1";
  const wss = new WebSocketServer({ port, host });
  wss.on("listening", () => {
    process.stdout.write(`fake-codex listening on ${LISTEN_URL}\n`);
  });
  wss.on("connection", (socket) => {
    sendFn = (obj) => {
      try { socket.send(JSON.stringify(obj)); } catch {}
    };
    socket.on("message", async (frame) => {
      let msg;
      try { msg = JSON.parse(String(frame)); } catch { return; }
      await handleMessage(msg);
    });
    socket.on("close", () => {
      if (interruptResolver) { interruptResolver(); interruptResolver = null; }
    });
  });
} else {
  // Stdio mode — managed CodexSession path.
  const rl = readline.createInterface({ input: process.stdin });
  rl.on("line", async (line) => {
    const trimmed = line.trim();
    if (!trimmed) return;
    let msg;
    try { msg = JSON.parse(trimmed); } catch { return; }
    await handleMessage(msg);
  });
  rl.on("close", () => process.exit(0));
}
