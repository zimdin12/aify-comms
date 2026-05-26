#!/usr/bin/env node
// Test double for hermes's tui_gateway WebSocket. Speaks JSON-RPC 2.0
// matching the subset of methods used by the aify-comms hermes resident
// channel: session.list, session.most_recent, prompt.submit, session.steer.
// Streams agent.message.delta / agent.message.end events back.
//
// Scripts (env FAKE_HERMES_SCRIPT):
//   - hello (default): one prompt.submit → "hello from hermes" stream → end
//   - enveloped       : same stream using real tui_gateway event envelopes
//   - turn_error      : prompt.submit accepted, then message.complete status=error
//   - busy           : prompt.submit returns 4009 "session busy"; session.steer accepted
//   - refuse         : prompt.submit returns 5000 error

import { WebSocketServer } from "ws";

const SCRIPT = String(process.env.FAKE_HERMES_SCRIPT || "hello");
const DELAY_MS = Number(process.env.FAKE_HERMES_DELAY_MS || 5);
const FIXED_SESSION_ID = process.env.FAKE_HERMES_SESSION_ID || "sess-fake-001";
const TOKEN = process.env.FAKE_HERMES_TOKEN || "test-token";

const cliArgs = process.argv.slice(2);
const listenIdx = cliArgs.indexOf("--listen");
const LISTEN_URL = listenIdx >= 0 ? String(cliArgs[listenIdx + 1] || "") : "";
if (!LISTEN_URL) {
  console.error("--listen ws://... required");
  process.exit(2);
}

function delay(ms) { return new Promise((r) => setTimeout(r, ms)); }

const url = new URL(LISTEN_URL);
const wss = new WebSocketServer({ port: Number(url.port), host: url.hostname || "127.0.0.1" });
wss.on("listening", () => process.stdout.write(`fake-hermes-gateway listening on ${LISTEN_URL}\n`));

wss.on("connection", (socket, req) => {
  const reqUrl = new URL(req.url, "ws://localhost");
  const presentedToken = reqUrl.searchParams.get("token");
  if (presentedToken !== TOKEN) {
    socket.close(4001, "bad token");
    return;
  }

  const send = (obj) => {
    try { socket.send(JSON.stringify(obj)); } catch { /* socket closing */ }
  };

  // Mirror the real gateway's behavior of emitting an event on connect.
  // The real gateway emits gateway.ready with skin metadata.
  send({ jsonrpc: "2.0", method: "event", params: { type: "gateway.ready", payload: {} } });

  socket.on("message", async (frame) => {
    let msg;
    try { msg = JSON.parse(String(frame)); } catch { return; }

    if (msg.method === "session.most_recent") {
      send({ jsonrpc: "2.0", id: msg.id, result: { session_id: FIXED_SESSION_ID, title: "fake", source: "acp" } });
      return;
    }
    if (msg.method === "session.list") {
      send({ jsonrpc: "2.0", id: msg.id, result: { sessions: [{ id: FIXED_SESSION_ID, title: "fake" }] } });
      return;
    }
    if (msg.method === "session.resume") {
      // Mirror tui_gateway/server.py:2386 session.resume: produce a fresh
      // in-memory short uuid sid bound to this WS connection. `resumed`
      // is the persisted session_key the caller passed in.
      const target = String(msg.params?.session_id || "").trim();
      const sid = `mem-${Math.random().toString(16).slice(2, 10)}`;
      send({ jsonrpc: "2.0", id: msg.id, result: { session_id: sid, resumed: target || FIXED_SESSION_ID, message_count: 0, messages: [] } });
      return;
    }
    if (msg.method === "session.create") {
      // Mirror tui_gateway/server.py:2237 session.create: allocate a fresh
      // in-memory session. Used as fallback when resume can't find the key.
      const sid = `mem-${Math.random().toString(16).slice(2, 10)}`;
      send({ jsonrpc: "2.0", id: msg.id, result: { session_id: sid } });
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
      if (SCRIPT === "turn_error") {
        await delay(DELAY_MS);
        send({
          jsonrpc: "2.0",
          method: "event",
          params: {
            type: "message.complete",
            session_id: sid,
            payload: { text: "provider unavailable", status: "error" },
          },
        });
        return;
      }
      const enveloped = SCRIPT === "enveloped";
      const emit = (type, payload) => {
        if (enveloped) {
          send({ jsonrpc: "2.0", method: "event", params: { type, session_id: sid, payload } });
        } else if (type === "message.delta") {
          send({ jsonrpc: "2.0", method: "agent.message.delta", params: { session_id: sid, delta: payload.text || "" } });
        } else if (type === "message.complete") {
          send({ jsonrpc: "2.0", method: "agent.message.end", params: { session_id: sid, text: payload.text || "" } });
        }
      };
      emit("message.start", {});
      for (const chunk of ["hello", " ", "from", " ", "hermes"]) {
        await delay(DELAY_MS);
        emit("message.delta", { text: chunk });
      }
      await delay(DELAY_MS);
      emit("message.complete", { text: "hello from hermes", status: "complete" });
      return;
    }
    if (msg.id !== undefined) {
      send({ jsonrpc: "2.0", id: msg.id, error: { code: -32601, message: `method not found: ${msg.method}` } });
    }
  });
});
