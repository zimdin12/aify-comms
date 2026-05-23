#!/usr/bin/env node
// Test double for `codex app-server` — speaks newline-delimited JSON-RPC
// over stdio matching the subset of codex's protocol that CodexSession
// uses (initialize/initialized, thread/start, thread/resume, turn/start,
// turn/interrupt, plus notifications: turn/started, turn/completed,
// item/agentMessage/delta, item/started, item/completed, error).
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

function send(obj) { process.stdout.write(JSON.stringify(obj) + "\n"); }
function delay(ms) { return new Promise((r) => setTimeout(r, ms)); }

let threadCounter = 0;
let turnCounter = 0;
const threads = new Map(); // threadId → { interrupted, activeTurnId }
let interruptResolver = null;

async function runTurn(reqId, threadId, params) {
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

const rl = readline.createInterface({ input: process.stdin });
rl.on("line", async (line) => {
  const trimmed = line.trim();
  if (!trimmed) return;
  let msg;
  try { msg = JSON.parse(trimmed); } catch { return; }

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
});

rl.on("close", () => process.exit(0));
