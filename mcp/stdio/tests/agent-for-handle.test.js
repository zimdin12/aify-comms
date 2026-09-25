// `agent-for-handle.mjs` is what a launcher runs on `--resume` to recover which agent owns a session.
// The launchers used to curl `/api/v1/agents` with no key, so with `API_KEY` set the lookup got a 401
// and recovery silently fell back (v0.7 docs review, D36). Run as a real child process, the way a
// launcher runs it, against a service that requires a key; the environment is sealed so neither the
// operator's key nor their registry can answer for the test.

import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { agentForHandle } from "../agent-for-handle.mjs";

const CLI = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "agent-for-handle.mjs");
const KEY = "test-key-for-handle";
const HOME = mkdtempSync(path.join(os.tmpdir(), "aify-agent-for-handle-"));

function serve() {
  const server = http.createServer((req, res) => {
    if (req.headers["x-api-key"] !== KEY) {
      res.writeHead(401, { "content-type": "application/json" }).end('{"detail":"no key"}');
      return;
    }
    res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify({
      agents: {
        "cc-coder": { runtime: "claude-code", sessionHandle: "sess-1" },
        "cx-coder": { runtime: "codex", sessionHandle: "sess-1" },
      },
    }));
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve(server)));
}

function run(url, runtime, handle, env) {
  const sealed = {
    PATH: process.env.PATH, SYSTEMROOT: process.env.SYSTEMROOT,
    HOME, USERPROFILE: HOME, AIFY_SERVICE_REGISTRY: path.join(HOME, "no-registry.json"), ...env,
  };
  return new Promise((resolve) => {
    execFile(process.execPath, [CLI, url, runtime, handle], { env: sealed, timeout: 15000 }, (error, stdout) => {
      resolve({ code: error ? error.code : 0, stdout });
    });
  });
}

test("with the key it can resolve, the owner of the handle is printed", async () => {
  const server = await serve();
  try {
    const url = `http://127.0.0.1:${server.address().port}`;
    const found = await run(url, "claude-code", "sess-1", { AIFY_API_KEY: KEY });
    assert.equal(found.code, 0);
    assert.equal(found.stdout, "cc-coder", "the runtime must narrow the match too");
    // THE DEFECT: without a key the service refuses, and the answer is nothing -- never a guess.
    const refused = await run(url, "claude-code", "sess-1", {});
    assert.equal(refused.code, 0, "a refusal must never fail the launch");
    assert.equal(refused.stdout, "");
  } finally {
    server.close();
  }
});

test("an unreachable service prints nothing and exits 0", async () => {
  const done = await run("http://127.0.0.1:9", "codex", "sess-1", { AIFY_API_KEY: KEY });
  assert.deepEqual(done, { code: 0, stdout: "" });
});

test("agentForHandle matches the handle AND the runtime, and sends the key it was given", async () => {
  const seen = [];
  const fetchImpl = async (url, init) => {
    seen.push({ url, key: init.headers["X-API-Key"] });
    return { ok: true, json: async () => ({ agents: {
      "cx-coder": { runtime: "codex", sessionHandle: "t-1" },
      "cc-coder": { runtime: "claude-code", session_handle: "t-1" },
    } }) };
  };
  const found = await agentForHandle({ url: "http://svc/", runtime: "claude-code", handle: "t-1", fetchImpl, keyFor: () => "k" });
  assert.equal(found, "cc-coder");
  assert.deepEqual(seen, [{ url: "http://svc/api/v1/agents", key: "k" }]);
  assert.equal(await agentForHandle({ url: "http://svc", runtime: "hermes", handle: "t-1", fetchImpl, keyFor: () => "" }), "");
});

process.on("exit", () => { try { rmSync(HOME, { recursive: true, force: true }); } catch { /* best effort */ } });
