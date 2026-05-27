#!/usr/bin/env node
// Test double for `hermes acp` — speaks newline-delimited JSON-RPC 2.0 over
// stdio in the camelCase wire format confirmed by
// docs/plans/notes/2026-05-23-hermes-acp-spike.md.
//
// Scripts (set via FAKE_HERMES_ACP_SCRIPT env):
//   - hello (default)      : emit "hello world\n" as chunks, stopReason=end_turn
//   - tool-call            : agent_thought_chunk → tool_call → tool_call_update(completed) → agent_message_chunk → end_turn
//   - refuse               : stopReason=refusal immediately
//   - crash-on-init        : exit(1) on initialize, so handshake fails
//   - client-callback      : during the turn, send fs/read_text_file to the
//                            bridge and wait for the response; emit the file's
//                            content back as an agent_message_chunk; end_turn.
//                            Path comes from FAKE_HERMES_ACP_CB_PATH.
//
// FAKE_HERMES_ACP_DELAY_MS controls the gap between session/update emissions
// (default 5ms — keeps tests fast).

import readline from "node:readline";

const SCRIPT = String(process.env.FAKE_HERMES_ACP_SCRIPT || "hello");
const DELAY_MS = Number(process.env.FAKE_HERMES_ACP_DELAY_MS || 5);

function send(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

function delay(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

let sessionCounter = 0;
const sessions = new Map(); // sessionId → { cancelled, pendingClient: Map<id, {resolve}> }
let nextClientReqId = 9000;

function getSession(id) {
  return sessions.get(id);
}

async function clientRequest(sessionId, method, params, { timeoutMs = 10000 } = {}) {
  const id = nextClientReqId++;
  return new Promise((resolve, reject) => {
    const session = getSession(sessionId);
    if (!session) return reject(new Error("session gone"));
    const timer = setTimeout(() => {
      session.pendingClient.delete(id);
      reject(new Error(`client request ${method} timed out`));
    }, timeoutMs);
    session.pendingClient.set(id, { resolve: (v) => { clearTimeout(timer); resolve(v); }, reject: (e) => { clearTimeout(timer); reject(e); } });
    send({ jsonrpc: "2.0", id, method, params });
  });
}

async function runPromptScript(reqId, sessionId, promptBlocks) {
  const session = getSession(sessionId);
  if (!session) {
    send({ jsonrpc: "2.0", id: reqId, error: { code: -32000, message: "unknown sessionId" } });
    return;
  }

  if (SCRIPT === "hello") {
    for (const chunk of ["hello", " ", "world", "\n"]) {
      if (session.cancelled) break;
      send({
        jsonrpc: "2.0",
        method: "session/update",
        params: {
          sessionId,
          update: { sessionUpdate: "agent_message_chunk", content: { type: "text", text: chunk } },
        },
      });
      await delay(DELAY_MS);
    }
    const stopReason = session.cancelled ? "cancelled" : "end_turn";
    send({ jsonrpc: "2.0", id: reqId, result: { stopReason } });
    return;
  }

  if (SCRIPT === "tool-call") {
    send({
      jsonrpc: "2.0",
      method: "session/update",
      params: {
        sessionId,
        update: { sessionUpdate: "agent_thought_chunk", content: { type: "text", text: "Reading README" } },
      },
    });
    await delay(DELAY_MS);
    send({
      jsonrpc: "2.0",
      method: "session/update",
      params: {
        sessionId,
        update: { sessionUpdate: "tool_call", toolCallId: "tc-1", title: "read_file", kind: "read", rawInput: { path: "README.md" } },
      },
    });
    await delay(DELAY_MS);
    send({
      jsonrpc: "2.0",
      method: "session/update",
      params: {
        sessionId,
        update: { sessionUpdate: "tool_call_update", toolCallId: "tc-1", title: "read_file", status: "completed", rawOutput: { length: 1234 } },
      },
    });
    await delay(DELAY_MS);
    send({
      jsonrpc: "2.0",
      method: "session/update",
      params: {
        sessionId,
        update: { sessionUpdate: "agent_message_chunk", content: { type: "text", text: "Done." } },
      },
    });
    send({ jsonrpc: "2.0", id: reqId, result: { stopReason: "end_turn" } });
    return;
  }

  if (SCRIPT === "refuse") {
    send({ jsonrpc: "2.0", id: reqId, result: { stopReason: "refusal" } });
    return;
  }

  if (SCRIPT === "client-callback") {
    const cbPath = process.env.FAKE_HERMES_ACP_CB_PATH || "nonexistent.txt";
    try {
      const response = await clientRequest(sessionId, "fs/read_text_file", { sessionId, path: cbPath });
      const content = String(response?.content ?? "");
      send({
        jsonrpc: "2.0",
        method: "session/update",
        params: {
          sessionId,
          update: { sessionUpdate: "agent_message_chunk", content: { type: "text", text: `read=${content}` } },
        },
      });
      send({ jsonrpc: "2.0", id: reqId, result: { stopReason: "end_turn" } });
    } catch (e) {
      send({ jsonrpc: "2.0", id: reqId, error: { code: -32000, message: String(e?.message || e) } });
    }
    return;
  }

  // Unknown script — empty end_turn
  send({ jsonrpc: "2.0", id: reqId, result: { stopReason: "end_turn" } });
}

const rl = readline.createInterface({ input: process.stdin });
rl.on("line", async (line) => {
  const trimmed = line.trim();
  if (!trimmed) return;
  let msg;
  try { msg = JSON.parse(trimmed); } catch { return; }

  // Response from the bridge to one of our client requests?
  if ((msg.result !== undefined || msg.error !== undefined) && typeof msg.id === "number") {
    for (const [, session] of sessions) {
      const pending = session.pendingClient.get(msg.id);
      if (pending) {
        session.pendingClient.delete(msg.id);
        if (msg.error) pending.reject(new Error(msg.error.message || String(msg.error.code)));
        else pending.resolve(msg.result);
        return;
      }
    }
    return;
  }

  if (msg.method === "initialize") {
    if (SCRIPT === "crash-on-init") {
      process.exit(1);
    }
    send({
      jsonrpc: "2.0",
      id: msg.id,
      result: {
        protocolVersion: 1,
        agentInfo: { name: "fake-hermes", version: "0" },
        agentCapabilities: {
          loadSession: true,
          promptCapabilities: { image: false },
          sessionCapabilities: { fork: {}, list: {}, resume: {} },
        },
        authMethods: [],
      },
    });
    return;
  }

  if (msg.method === "session/new") {
    sessionCounter += 1;
    const sessionId = `fake-sess-${sessionCounter}`;
    sessions.set(sessionId, { cancelled: false, pendingClient: new Map() });
    send({
      jsonrpc: "2.0",
      id: msg.id,
      result: {
        sessionId,
        models: { availableModels: [], currentModelId: "" },
        modes: { availableModes: [], currentModeId: "default" },
      },
    });
    return;
  }

  if (msg.method === "session/prompt") {
    runPromptScript(msg.id, msg.params?.sessionId, msg.params?.prompt).catch((e) => {
      send({ jsonrpc: "2.0", id: msg.id, error: { code: -32000, message: String(e?.message || e) } });
    });
    return;
  }

  if (msg.method === "session/cancel") {
    const s = sessions.get(msg.params?.sessionId);
    if (s) s.cancelled = true;
    send({ jsonrpc: "2.0", id: msg.id, result: null });
    return;
  }

  if (msg.method === "session/close") {
    sessions.delete(msg.params?.sessionId);
    send({ jsonrpc: "2.0", id: msg.id, result: null });
    return;
  }

  // Unknown method with id → method-not-found
  if (msg.id !== undefined) {
    send({ jsonrpc: "2.0", id: msg.id, error: { code: -32601, message: `method not found: ${msg.method}` } });
  }
});

rl.on("close", () => process.exit(0));
