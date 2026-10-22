// A minimal stdio MCP server that declares the claude/channel capability and, 5 s after it is initialized, pushes one
// channel event. No SDK: line-delimited JSON-RPC, enough for Claude Code to register it as a channel.
import { appendFileSync } from "node:fs";
import { createInterface } from "node:readline";

const log = (line) => appendFileSync("/tmp/aify-channel.log", `${new Date().toISOString()} ${line}\n`);
const send = (message) => process.stdout.write(`${JSON.stringify(message)}\n`);

createInterface({ input: process.stdin }).on("line", (line) => {
  let message;
  try { message = JSON.parse(line); } catch { return; }
  log(`in ${message.method ?? "response"}`);
  if (message.method === "initialize") {
    send({ jsonrpc: "2.0", id: message.id, result: {
      protocolVersion: "2025-06-18",
      capabilities: { experimental: { "claude/channel": {} } },
      serverInfo: { name: "aify-channel", version: "0.0.1" },
      instructions: "Events from aify arrive as <channel source=\"aify-channel\">. Read and act.",
    } });
  } else if (message.method === "notifications/initialized") {
    setTimeout(() => {
      send({ jsonrpc: "2.0", method: "notifications/claude/channel",
        params: { content: "aify channel proof event", meta: { kind: "proof" } } });
      log("pushed the proof event");
    }, 5000);
  } else if (message.id !== undefined) {
    send({ jsonrpc: "2.0", id: message.id, error: { code: -32601, message: "method not found" } });
  }
});
