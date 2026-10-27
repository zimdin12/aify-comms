#!/usr/bin/env node
// On a launch whose comms tools come over HTTP, the Claude channel is the only local comms process, so it offers the two
// tools that need the agent's disk: comms_share_file and comms_save_file (shared-file-transfer.mjs). A file must cross
// both ways byte-identical: a hand-built upload once added two framing bytes to every shared file.
//
// THE REAL ENTRY, RUN AS CLAUDE RUNS IT, spoken to over MCP against a stand-in service that keeps what it is sent. The
// uploaded bytes are cut out of the raw request the way a strict multipart parser would, so a lenient one cannot hide
// stray framing.

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { SHARED_FILE_TOOLS, callSharedFileTool } from "../shared-file-transfer.mjs";
import { sealedChildEnv } from "./_child-env.mjs";

const CHANNEL = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "claude-channel.js");
const AGENT = "files-witness";
const PAYLOAD = Buffer.from([0x0d, 0x0a, 0x00, 0xff, 0x2d, 0x2d, 0x0d, 0x0a, 0x0d, 0x0a, 0x41, 0x0a, 0x80, 0x0d]);

/** The file part's bytes: after its header block's first blank line, up to the CRLF before the closing boundary. */
function filePart(raw, contentType) {
  const boundary = `--${/boundary=([^;]+)/.exec(contentType)[1]}`;
  const header = raw.indexOf(Buffer.from('name="file"'));
  assert.ok(header >= 0, "the upload has no file part");
  const start = raw.indexOf(Buffer.from("\r\n\r\n"), header) + 4;
  const end = raw.indexOf(Buffer.from(`\r\n${boundary}`), start);
  return raw.subarray(start, end);
}

async function service() {
  const stored = new Map();
  const uploads = [];
  const server = http.createServer((req, res) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {
      const raw = Buffer.concat(chunks);
      if (req.method === "POST" && req.url === "/api/v1/shared") {
        const bytes = filePart(raw, req.headers["content-type"]);
        uploads.push({ raw: raw.toString("latin1"), bytes });
        stored.set("blob.bin", bytes);
        res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify({ ok: true, name: "blob.bin" }));
        return;
      }
      const name = decodeURIComponent((/^\/api\/v1\/shared\/(.+)$/.exec(req.url) || [])[1] || "");
      if (req.method === "GET" && name) {
        if (!stored.has(name)) { res.writeHead(404, { "content-type": "application/json" }).end(JSON.stringify({ detail: "not found" })); return; }
        res.writeHead(200, { "content-type": "application/octet-stream" }).end(stored.get(name));
        return;
      }
      const answer = req.url === "/api/v1/dispatch/controls/claim" ? { controls: [] } : req.url === "/api/v1/agents" ? { ok: true, agentId: AGENT } : {};
      res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(answer));
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { url: `http://127.0.0.1:${server.address().port}`, uploads, close: () => new Promise((r) => server.close(r)) };
}

async function until(condition, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  while (!condition() && Date.now() < deadline) await new Promise((r) => setTimeout(r, 50));
}

/** A channel launch spoken to over MCP: `request(method, params)` answers with the result or the error. */
async function launch(commsMcp) {
  const svc = await service();
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-files-"));
  const [temp, state, home] = ["temp", "state", "home"].map((d) => path.join(dir, d));
  for (const d of [temp, state, home]) fs.mkdirSync(d, { recursive: true });
  if (!commsMcp) fs.writeFileSync(path.join(temp, `aify-agent-${process.pid}`), JSON.stringify({ agentId: AGENT, pid: process.pid }));
  const child = spawn(process.execPath, [CHANNEL], {
    env: sealedChildEnv({
      TEMP: temp, TMP: temp, XDG_STATE_HOME: state, HOME: home, USERPROFILE: home,
      AIFY_SERVER_URL: svc.url, CLAUDE_MCP_SERVER_URL: svc.url, AIFY_CLAIM_WAIT_MS: "0", AIFY_COMMS_CHANNEL_POLL_MS: "100",
      AIFY_AGENT_ID: AGENT, AIFY_RUNTIME: "claude-code", AIFY_SESSION_MODE: "resident", AIFY_COMMS_MCP: commsMcp,
    }),
    cwd: dir,
    stdio: ["pipe", "pipe", "pipe"],
  });
  const answers = new Map();
  let buffer = "";
  child.stdout.on("data", (chunk) => {
    buffer += chunk;
    let at;
    while ((at = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, at); buffer = buffer.slice(at + 1);
      try { const m = JSON.parse(line); if (m.id !== undefined) answers.set(m.id, m); } catch { /* not a message */ }
    }
  });
  let id = 0;
  const request = async (method, params = {}) => {
    const mine = ++id;
    child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", id: mine, method, params })}\n`);
    await until(() => answers.has(mine));
    return answers.get(mine);
  };
  const init = await request("initialize", { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "witness", version: "0" } });
  child.stdin.write(`${JSON.stringify({ jsonrpc: "2.0", method: "notifications/initialized" })}\n`);
  // The tools act as the agent the binding names, which this launch's registration writes.
  await until(() => fs.existsSync(path.join(temp, `aify-agent-${process.pid}`)));
  const exited = new Promise((resolve) => child.on("exit", resolve));
  const stop = async () => {
    child.stdin.end();
    const kill = setTimeout(() => child.kill(), 10_000);
    await exited;
    clearTimeout(kill);
    await svc.close();
    fs.rmSync(dir, { recursive: true, force: true });
  };
  return { init, request, svc, dir, stop };
}

test("an http launch's channel shares a local file and saves it back, byte-identical", async () => {
  const run = await launch("http");
  try {
    assert.ok(run.init.result?.capabilities?.tools, "the channel does not declare tools");
    const listed = (await run.request("tools/list")).result.tools.map((t) => t.name).sort();
    assert.deepEqual(listed, ["comms_save_file", "comms_share_file"]);

    const source = path.join(run.dir, "blob.bin");
    fs.writeFileSync(source, PAYLOAD);
    const shared = (await run.request("tools/call", { name: "comms_share_file", arguments: { filePath: source } })).result;
    assert.equal(shared.isError, undefined, shared.content[0].text);
    assert.equal(run.svc.uploads.length, 1);
    assert.ok(run.svc.uploads[0].bytes.equals(PAYLOAD), `uploaded ${run.svc.uploads[0].bytes.toString("hex")}`);
    assert.match(run.svc.uploads[0].raw, new RegExp(`name="from_agent"\\r\\n\\r\\n${AGENT}\\r\\n`), "the upload is not sent as the bound agent");

    const target = path.join(run.dir, "saved.bin");
    const saved = (await run.request("tools/call", { name: "comms_save_file", arguments: { name: "blob.bin", savePath: target } })).result;
    assert.equal(saved.isError, undefined, saved.content[0].text);
    assert.ok(fs.readFileSync(target).equals(PAYLOAD), "the saved file differs from the shared one");

    const missing = (await run.request("tools/call", { name: "comms_save_file", arguments: { name: "gone.bin", savePath: target } })).result;
    assert.deepEqual([missing.isError, missing.content[0].text], [true, 'Artifact "gone.bin" not found.']);
  } finally {
    await run.stop();
  }
});

test("control: a launch still running server.js lists no tools on its channel", async () => {
  const run = await launch(undefined);
  try {
    assert.equal(run.init.result?.capabilities?.tools, undefined);
    assert.ok((await run.request("tools/list")).error, "tools/list answered on a channel with no tools");
  } finally {
    await run.stop();
  }
});

test("refusals before any request: no bound agent, an unreadable file, an unknown tool", async () => {
  const requests = [];
  const fetchImpl = async (...args) => { requests.push(args); throw new Error("no request was expected"); };
  const at = { serverUrl: "http://127.0.0.1:9", fetchImpl };
  const unbound = await callSharedFileTool("comms_share_file", { filePath: CHANNEL }, { ...at, agentId: "" });
  assert.deepEqual([unbound.isError, unbound.content[0].text], [true, "This session is not registered yet, so a share would have no sender."]);
  const unreadable = await callSharedFileTool("comms_share_file", { filePath: path.join(os.tmpdir(), "aify-no-such-file-0911") }, { ...at, agentId: AGENT });
  assert.equal(unreadable.isError, true);
  assert.match(unreadable.content[0].text, /^Cannot read .*: ENOENT$/);
  const unknown = await callSharedFileTool("comms_nope", {}, { ...at, agentId: AGENT });
  assert.equal(unknown.isError, true);
  assert.deepEqual(requests, [], "a refusal reached the service");
  assert.deepEqual(SHARED_FILE_TOOLS.map((t) => t.inputSchema.required), [["filePath"], ["name"]]);
});
