#!/usr/bin/env node
// Where a managed hermes agent's model and effort can be set, on the path aify-comms actually runs.
//
// hermes-aify runs every agent-id launch through a per-agent gateway host, `hermes dashboard`, spawned
// by aify-comms' bridge with the launcher's environment (mcp/stdio/hermes-gateway.mjs). The agent's
// turns run IN that host, not in the visible TUI, so a `-m` or `--reasoning` handed to the TUI decides
// nothing. hermes' source (tui_gateway/server.py `_env_model_seed`) reads HERMES_MODEL /
// HERMES_INFERENCE_MODEL as the host's model seed, and has no environment lever for effort: effort
// comes from config.yaml, `session.create {reasoning_effort}`, or `config.set reasoning` per session.
// This runs a SEALED host and asks it, instead of trusting that reading:
//
//   1. the host started with HERMES_INFERENCE_MODEL=probe/env-model; `session.create {}`: which model?
//   2. `session.create {model, reasoning_effort}`: are the per-session values taken?
//   3. `config.set {key: "reasoning", value: "xhigh", session_id}` on session 1: does its effort change?
//      Read back with `config.get {key: "reasoning", session_id}` before and after, and for session 2.
//   4. the same `config.set` with a session id the host does not know: refused, and the host-wide
//      effort (`config.get` with no session) unchanged? A miss must never become a global write.
//
// PROBE_HERMES_HOME names a home to keep between runs: a fresh one spends minutes installing hermes'
// isolated runtime into itself before the host serves anything.
//
// SEALED: a temporary HERMES_HOME with no credentials, a port the OS chose, outside aify-comms' gateway
// range, the whole process tree killed at the end. No aify-comms, aify-env or agent is involved, and
// nothing is sent to a model. Run from the aify-comms checkout:
//   node docs/superpowers/plans/evidence/2026-10-01-p6/hermes-model-probe.mjs

import { spawn, spawnSync } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../../..");
const require = createRequire(path.join(ROOT, "mcp/stdio/package.json"));
const { WebSocket } = require("ws");

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const freePort = () => new Promise((resolve) => {
  const server = net.createServer().listen(0, "127.0.0.1", () => { const { port } = server.address(); server.close(() => resolve(port)); });
});

const keep = process.env.PROBE_HERMES_HOME || "";
const home = keep || fs.mkdtempSync(path.join(os.tmpdir(), "hermes-model-probe-"));
fs.mkdirSync(home, { recursive: true });
const port = await freePort();
const env = { ...process.env, HERMES_HOME: home, HERMES_INFERENCE_MODEL: "probe/env-model", HERMES_DASHBOARD_TUI: "1" };
for (const key of Object.keys(env)) if (/^(AIFY_|HERMES_TUI_GATEWAY|HERMES_SESSION|HERMES_MODEL$)/.test(key)) delete env[key];
const log = path.join(home, "host.log");
const host = spawn("hermes", ["dashboard", "--port", String(port), "--host", "127.0.0.1", "--no-open", "--skip-build"],
  { env, stdio: ["ignore", fs.openSync(log, "w"), fs.openSync(log, "a")], shell: process.platform === "win32", windowsHide: true });
const report = { hermes: spawnSync("hermes", ["--version"], { encoding: "utf8", shell: true }).stdout.split("\n")[0], port, steps: [] };

function stop() {
  if (process.platform === "win32") spawnSync("taskkill", ["/PID", String(host.pid), "/T", "/F"], { stdio: "ignore" });
  else host.kill("SIGKILL");
}

try {
  let token = "";
  for (const end = Date.now() + 900000; !token && Date.now() < end; await sleep(1000)) {
    try {
      const body = await (await fetch(`http://127.0.0.1:${port}/`)).text();
      token = (/__HERMES_SESSION_TOKEN__\s*=\s*"([^"]+)"/.exec(body) || [])[1] || "";
    } catch { /* not up yet */ }
  }
  if (!token) throw new Error(`the host never served its token; log tail: ${fs.readFileSync(log, "utf8").slice(-1500)}`);

  const ws = new WebSocket(`ws://127.0.0.1:${port}/api/ws?token=${token}`);
  const inbox = [];
  ws.on("message", (data) => { try { inbox.push(JSON.parse(data.toString())); } catch { /* not JSON */ } });
  await new Promise((resolve, reject) => { ws.once("open", resolve); ws.once("error", reject); });
  let nextId = 1;
  const call = async (method, params) => {
    const id = nextId++;
    ws.send(JSON.stringify({ jsonrpc: "2.0", id, method, params }));
    for (const end = Date.now() + 30000; Date.now() < end; await sleep(100)) {
      const answer = inbox.find((m) => m.id === id);
      if (answer) return answer;
    }
    return { timeout: true };
  };
  const infoFor = (sid) => inbox.filter((m) => m.method === "event" && m.params?.session_id === sid && m.params?.type === "session.info")
    .map((m) => ({ model: m.params.payload?.model, reasoning_effort: m.params.payload?.reasoning_effort })).at(-1) ?? null;
  const pick = (answer) => answer.error ? { error: answer.error } : {
    session_id: answer.result?.session_id, model: answer.result?.info?.model ?? answer.result?.model,
    reasoning_effort: answer.result?.info?.reasoning_effort ?? answer.result?.reasoning_effort,
    resultKeys: Object.keys(answer.result || {}), infoKeys: Object.keys(answer.result?.info || {}),
  };

  const first = await call("session.create", {});
  report.steps.push({ step: "1 session.create {} with HERMES_INFERENCE_MODEL=probe/env-model", answer: pick(first) });
  const second = await call("session.create", { model: "probe/param-model", reasoning_effort: "low" });
  report.steps.push({ step: "2 session.create {model, reasoning_effort: low}", answer: pick(second) });
  const sid = first.result?.session_id;
  const effortOf = async (sessionId) => {
    const got = await call("config.get", sessionId ? { key: "reasoning", session_id: sessionId } : { key: "reasoning" });
    return got.error ? { error: got.error } : got.result?.value;
  };
  if (sid) {
    const before = await effortOf(sid);
    const set = await call("config.set", { key: "reasoning", value: "xhigh", session_id: sid });
    await sleep(1500);
    report.steps.push({ step: "3 config.set reasoning xhigh on session 1", before, answer: set.error ? { error: set.error } : set.result,
      after: await effortOf(sid), session2: await effortOf(second.result?.session_id), sessionInfoAfter: infoFor(sid) });
  }
  const hostBefore = await effortOf("");
  const miss = await call("config.set", { key: "reasoning", value: "max", session_id: "no-such-session" });
  report.steps.push({ step: "4 config.set reasoning max on an unknown session", answer: miss.error ? { error: miss.error } : miss.result,
    hostWideBefore: hostBefore, hostWideAfter: await effortOf("") });
  report.methodsSeen = [...new Set(inbox.map((m) => m.method || (m.result ? "result" : "error")))];
  report.eventTypes = [...new Set(inbox.filter((m) => m.method === "event").map((m) => m.params?.type))];
  ws.close();
} catch (error) {
  report.error = String(error?.message || error);
} finally {
  stop();
  await sleep(2000);
  if (!keep) fs.rmSync(home, { recursive: true, force: true, maxRetries: 5 });
}
console.log(JSON.stringify(report, null, 1));
process.exit(0);
