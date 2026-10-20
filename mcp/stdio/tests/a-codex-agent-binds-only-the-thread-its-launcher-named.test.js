// A codex agent registers the thread its launcher named (CODEX_THREAD_ID / AIFY_SESSION_HANDLE), or none.
//
// THE INCIDENT. On 2026-10-19 a fresh agent in C:/Docker/aify-dashboard was bound to the operator's own
// Codex Desktop conversation: its first dispatch called thread/resume on it and wrote into its rollout.
// 211d2953 closed the app-server "newest thread anywhere" fallback. Review found two more guesses feeding
// the same registration: with no AIFY_CODEX_APP_SERVER_URL the adapter returned the newest rollout under
// ~/.codex/sessions, from any workspace; and with a live app server, registration took the newest thread
// whose cwd matched. A matching cwd is affinity, not ownership. Both are gone; these tests hold that.
//
// WHAT RUNS. The real registration code, in a child: `makeAutoRegister` (the startup path) and the real
// `comms_register` handler, against a fake service on 127.0.0.2 whose POST body we read. The adapter is
// the real CodexAdapter, resolved from AIFY_RUNTIME at module load.
//
// SEALED. HOME and USERPROFILE point at a temp home whose `.codex/sessions` holds the rollouts below, so
// nothing reads the operator's sessions. XDG_STATE_HOME holds the runtime markers (`listRuntimeMarkers`
// deletes markers of dead pids, so it must never see the real directory) and TEMP takes the binding file.
// Every codex variable the bridge reads is set or deleted per case. The child's own output proves the
// rollout fixture is where the adapter looks (`visible`), so an empty handle is a refusal, not a miss.

import assert from "node:assert/strict";
import { execFileSync, spawn } from "node:child_process";
import { once } from "node:events";
import fs from "node:fs";
import { createServer } from "node:net";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { fileURLToPath, pathToFileURL } from "node:url";
import { sealedChildEnv } from "./_child-env.mjs";

const STDIO = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const FAKE = path.join(STDIO, "tests", "fixtures", "fake-codex-app-server.mjs");
const url = (file) => JSON.stringify(pathToFileURL(path.join(STDIO, file)).href);

const ROOT = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), "aify-codex-owner-")));
const HOME = path.join(ROOT, "home");
const SESSIONS = path.join(HOME, ".codex", "sessions");
const STATE = path.join(ROOT, "state");
const MARKERS = path.join(STATE, "aify-comms", "runtime-markers");
const TEMP = path.join(ROOT, "temp");
const WORKSPACE = path.join(ROOT, "this-workspace");
const ELSEWHERE = path.join(ROOT, "another-workspace");
for (const dir of [SESSIONS, MARKERS, TEMP, WORKSPACE, ELSEWHERE]) fs.mkdirSync(dir, { recursive: true });

const FOREIGN_ROLLOUT = "11111111-2222-4333-8444-555555555555";
const LOCAL_ROLLOUT = "66666666-7777-4888-8999-aaaaaaaaaaaa";
const EXPLICIT = "explicit-thread-from-the-launcher";

// A rollout as codex writes it: date-sharded, the thread id in the name, and a session_meta first line
// carrying the workspace it ran in. `ageSeconds` sets the mtime, which is what the old scan ranked by.
function writeRollout(id, cwd, ageSeconds) {
  const dir = path.join(SESSIONS, "2026", "10", "20");
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, `rollout-2026-10-20T00-00-00-${id}.jsonl`);
  fs.writeFileSync(file, JSON.stringify({ type: "session_meta", payload: { id, cwd } }) + "\n");
  const when = Date.now() / 1000 - ageSeconds;
  fs.utimesSync(file, when, when);
  return file;
}

function reset() {
  for (const dir of [SESSIONS, MARKERS]) {
    fs.rmSync(dir, { recursive: true, force: true });
    fs.mkdirSync(dir, { recursive: true });
  }
}

// A live marker the cwd-less walk finds, as the existing discovery test writes them; the pid is this
// process, which is alive for as long as the child runs.
function writeMarker(name, appServerUrl) {
  fs.writeFileSync(path.join(MARKERS, `codex-${name}.json`),
    JSON.stringify({ pid: process.pid, runtime: "codex", appServerUrl }));
}

function pickPort() {
  return new Promise((resolve, reject) => {
    const s = createServer();
    s.listen(0, "127.0.0.2", () => { const { port } = s.address(); s.close(() => resolve(port)); });
    s.on("error", reject);
  });
}

// The fake reports its own process cwd as every thread's cwd, so `cwd` is the workspace its thread is in.
async function withFakeAppServer(thread, cwd, fn) {
  const appServerUrl = `ws://127.0.0.2:${await pickPort()}`;
  const child = spawn(process.execPath, [FAKE, "--listen", appServerUrl], {
    cwd,
    env: sealedChildEnv({ FAKE_CODEX_RESIDENT_THREAD: thread, FAKE_CODEX_THREAD_LIST_KEY: "data" }),
    stdio: ["ignore", "pipe", "pipe"],
  });
  try {
    await once(child.stdout, "data");
    return await fn(appServerUrl);
  } finally {
    child.kill("SIGTERM");
    await once(child, "exit").catch(() => {});
  }
}

// Runs one registration in a child and returns { post, visible, text }. `via` is "auto" (startup) or
// "tool" (comms_register with `args`). `env` holds the codex variables for this case.
function register({ via, env = {}, args = {} }) {
  const script = `
    import http from "node:http";
    const requests = [];
    const srv = http.createServer((req, res) => {
      let body = "";
      req.on("data", (c) => { body += c; });
      req.on("end", () => {
        requests.push({ method: req.method, url: req.url, body: body ? JSON.parse(body) : null });
        res.writeHead(200, { "content-type": "application/json" });
        if (req.method === "POST" && req.url.endsWith("/agents")) {
          return res.end(JSON.stringify({ agentId: "codex-owner", role: "coder", runtime: "codex", sessionMode: "resident", capabilities: {} }));
        }
        res.end("{}");
      });
    });
    await new Promise((r) => srv.listen(0, "127.0.0.2", r));
    process.env.AIFY_SERVER_URL = "http://127.0.0.2:" + srv.address().port;
    process.env.CLAUDE_MCP_SERVER_URL = process.env.AIFY_SERVER_URL;
    // The probe asks by id, and the adapter prefers CODEX_THREAD_ID over the id it is asked for, so
    // the variable is set aside for the probe and put back before anything registers.
    const { CodexAdapter } = await import(${url("adapters/codex.js")});
    const threadEnv = process.env.CODEX_THREAD_ID;
    delete process.env.CODEX_THREAD_ID;
    const visible = await new CodexAdapter()._resolveRolloutPath({ sessionId: ${JSON.stringify(FOREIGN_ROLLOUT)} })
      || await new CodexAdapter()._resolveRolloutPath({ sessionId: ${JSON.stringify(LOCAL_ROLLOUT)} });
    if (threadEnv !== undefined) process.env.CODEX_THREAD_ID = threadEnv;
    let text = null;
    if (${JSON.stringify(via)} === "auto") {
      const { makeAutoRegister } = await import(${url("auto-registration.mjs")});
      await makeAutoRegister({ ensureDispatchLoop: () => {} })(0);
    } else {
      const { registerRegistrationTool } = await import(${url("registration-tool.mjs")});
      const { z } = await import("zod");
      const tools = new Map();
      registerRegistrationTool({ tool: (name, d, s, handler) => tools.set(name, handler) }, z, { ensureDispatchLoop: () => {} });
      const out = await tools.get("comms_register")(${JSON.stringify({ agentId: "codex-owner", role: "coder", runtime: "codex", cwd: WORKSPACE, ...args })});
      text = out?.content?.[0]?.text || null;
    }
    srv.close();
    const post = requests.find((q) => q.method === "POST" && q.url === "/api/v1/agents")?.body || null;
    process.stdout.write(JSON.stringify({ post, visible, text }));
    process.exit(0);
  `;
  const out = execFileSync(process.execPath, ["--input-type=module", "-e", script], {
    cwd: STDIO,
    env: sealedChildEnv({
      HOME, USERPROFILE: HOME, XDG_STATE_HOME: STATE, TEMP, TMP: TEMP,
      AIFY_SERVER_URL: "", CLAUDE_MCP_SERVER_URL: "", CLAUDE_MCP_MESSAGES_DIR: TEMP,
      AIFY_AGENT_ID: "codex-owner", AIFY_COMMS_AGENT_ID: undefined, AIFY_AGENT_ROLE: "coder",
      AIFY_RUNTIME: "codex", AIFY_AGENT_RUNTIME: undefined, AIFY_AGENT_CWD: WORKSPACE,
      AIFY_SESSION_MODE: "resident", AIFY_MANAGED_DISPATCH: undefined, AIFY_FORCE_REGISTER: undefined,
      AIFY_CHANNELS_ENABLED: undefined, CODEX_HOME: undefined, CODEX_SANDBOX: undefined,
      CODEX_THREAD_ID: undefined, AIFY_SESSION_HANDLE: undefined,
      AIFY_CODEX_APP_SERVER_URL: undefined, AIFY_CODEX_REMOTE_AUTH_TOKEN_ENV: undefined,
      ...env,
    }),
    encoding: "utf-8", stdio: ["ignore", "pipe", "pipe"], timeout: 60_000,
  });
  const result = JSON.parse(out);
  assert.ok(result.post, "the registration must reach the service, or nothing below was tested");
  return result;
}

const sessionHandleOf = (r) => r.post.sessionHandle;

// ── (a) another workspace's newest rollout and live thread: no handle ───────────────────────────

test("(a) no explicit id, no app-server URL: another workspace's newest rollout is not this agent's", async () => {
  reset();
  const rollout = writeRollout(FOREIGN_ROLLOUT, ELSEWHERE, 0);
  await withFakeAppServer("thread-in-another-workspace", ELSEWHERE, async (appServerUrl) => {
    writeMarker("elsewhere", appServerUrl);
    const r = register({ via: "auto" });
    assert.equal(r.visible, rollout, "fixture: the rollout must be where the adapter looks");
    assert.equal(sessionHandleOf(r), "", JSON.stringify(r.post));
    assert.equal(r.post.runtimeConfig?.appServerUrl, undefined, "no handle binds no server either");
  });
});

test("(a) no explicit id, live app-server URL: neither the newest rollout nor the server's thread is taken", async () => {
  reset();
  writeRollout(FOREIGN_ROLLOUT, ELSEWHERE, 0);
  await withFakeAppServer("thread-in-another-workspace", ELSEWHERE, async (appServerUrl) => {
    const r = register({ via: "auto", env: { AIFY_CODEX_APP_SERVER_URL: appServerUrl } });
    assert.equal(sessionHandleOf(r), "", JSON.stringify(r.post));
    assert.equal(r.post.runtimeConfig?.appServerUrl, appServerUrl, "the launcher's own server is still configured");
  });
});

// ── (b) the same in THIS workspace: a cwd match is still not ownership ──────────────────────────

test("(b) no explicit id, no app-server URL: a cwd-matching rollout and live thread are still not taken", async () => {
  reset();
  writeRollout(FOREIGN_ROLLOUT, ELSEWHERE, 60);
  const rollout = writeRollout(LOCAL_ROLLOUT, WORKSPACE, 0);
  await withFakeAppServer("thread-in-this-workspace", WORKSPACE, async (appServerUrl) => {
    writeMarker("here", appServerUrl);
    const r = register({ via: "auto" });
    assert.ok(r.visible, "fixture: a rollout must be where the adapter looks");
    assert.ok(fs.existsSync(rollout));
    assert.equal(sessionHandleOf(r), "", JSON.stringify(r.post));
  });
});

test("(b) no explicit id, live app-server URL: the server's cwd-matching thread is not taken", async () => {
  reset();
  writeRollout(LOCAL_ROLLOUT, WORKSPACE, 0);
  await withFakeAppServer("thread-in-this-workspace", WORKSPACE, async (appServerUrl) => {
    const r = register({ via: "auto", env: { AIFY_CODEX_APP_SERVER_URL: appServerUrl } });
    assert.equal(sessionHandleOf(r), "", JSON.stringify(r.post));
  });
});

test("(b) comms_register with no handle and a cwd-matching live server takes no thread", async () => {
  reset();
  writeRollout(LOCAL_ROLLOUT, WORKSPACE, 0);
  await withFakeAppServer("thread-in-this-workspace", WORKSPACE, async (appServerUrl) => {
    const r = register({ via: "tool", args: { appServerUrl } });
    assert.equal(sessionHandleOf(r), "", JSON.stringify(r.post));
    assert.match(r.text, /no thread was auto-bound/, "the agent is told how to name its thread");
  });
});

// ── (c) the positive control: an explicit id is used as given ───────────────────────────────────

test("(c) an explicit CODEX_THREAD_ID is the handle, and binds the live server that holds it", async () => {
  reset();
  writeRollout(FOREIGN_ROLLOUT, ELSEWHERE, 0);
  await withFakeAppServer(EXPLICIT, WORKSPACE, async (appServerUrl) => {
    writeMarker("holder", appServerUrl);
    const r = register({ via: "auto", env: { CODEX_THREAD_ID: EXPLICIT } });
    assert.equal(r.visible.endsWith(`${FOREIGN_ROLLOUT}.jsonl`), true, "fixture: a newer rollout is present");
    assert.equal(sessionHandleOf(r), EXPLICIT, JSON.stringify(r.post));
    // The live server is found by the id it holds. This is also the fixture's control: the marker and
    // the server are reachable, so the empty handles above are refusals.
    assert.equal(r.post.runtimeConfig?.appServerUrl, appServerUrl, JSON.stringify(r.post));
  });
});

test("(c) an explicit id is the handle on the app-server route and through comms_register", async () => {
  reset();
  writeRollout(LOCAL_ROLLOUT, WORKSPACE, 0);
  await withFakeAppServer("thread-in-this-workspace", WORKSPACE, async (appServerUrl) => {
    const env = { AIFY_CODEX_APP_SERVER_URL: appServerUrl, CODEX_THREAD_ID: EXPLICIT, AIFY_SESSION_HANDLE: EXPLICIT };
    assert.equal(sessionHandleOf(register({ via: "auto", env })), EXPLICIT);
    assert.equal(sessionHandleOf(register({ via: "tool", env: { CODEX_THREAD_ID: EXPLICIT } })), EXPLICIT,
      "comms_register fills the handle from CODEX_THREAD_ID");
    assert.equal(sessionHandleOf(register({ via: "tool", args: { appServerUrl, sessionHandle: EXPLICIT } })), EXPLICIT,
      "an explicit sessionHandle argument is used as given");
  });
});

test.after(() => {
  try { fs.rmSync(ROOT, { recursive: true, force: true }); } catch { /* best effort */ }
});
