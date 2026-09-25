// comms_listen read a 401 or 404 as "No messages received (timeout)": the refusal's body has no
// `messages`, and the tool never looked at the status (v0.7 scan B17).
import assert from "node:assert/strict";
import test from "node:test";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const TOOLS = pathToFileURL(path.join(HERE, "..", "inbox-tools.mjs")).href;

function listenAgainst(status, payload) {
  return new Promise((resolve, reject) => {
    const server = http.createServer((_req, res) => {
      res.writeHead(status, { "content-type": "application/json" });
      res.end(JSON.stringify(payload));
    });
    server.listen(0, "127.0.0.1", () => {
      const script = `
        const { registerInboxTools } = await import(${JSON.stringify(TOOLS)});
        const { z } = await import("zod");
        const tools = {};
        registerInboxTools({ tool: (name, _d, _s, h) => { tools[name] = h; } }, z);
        const res = await tools.comms_listen({ agentId: "listener", timeout: 1 });
        process.stdout.write(JSON.stringify({ isError: !!res.isError, text: res.content[0].text }));
      `;
      const child = spawn(process.execPath, ["--input-type=module", "-e", script], {
        cwd: HERE,
        env: {
          PATH: process.env.PATH, SystemRoot: process.env.SystemRoot || "",
          HOME: os.tmpdir(), USERPROFILE: os.tmpdir(),
          AIFY_SERVER_URL: `http://127.0.0.1:${server.address().port}`,
          AIFY_SERVICE_REGISTRY: path.join(os.tmpdir(), "no-such-registry.json"),
        },
      });
      let out = "", err = "";
      child.stdout.on("data", (d) => { out += d; });
      child.stderr.on("data", (d) => { err += d; });
      child.on("exit", () => { server.close(); try { resolve(JSON.parse(out)); } catch { reject(new Error(err || out)); } });
    });
  });
}

test("a refused listen says it was refused", async () => {
  const res = await listenAgainst(401, { detail: "Invalid API key" });
  assert.equal(res.isError, true, res.text);
  assert.match(res.text, /HTTP 401/);
});

test("control: an empty answer is still the timeout", async () => {
  const res = await listenAgainst(200, { messages: [], total: 0 });
  assert.match(res.text, /No messages received \(timeout\)/);
});
