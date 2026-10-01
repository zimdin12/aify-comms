#!/usr/bin/env node
// Which process decides a resident codex agent's model and effort: the app-server, or the TUI?
//
// codex-aify starts `codex app-server` and then `codex --remote <url>` (the TUI). Both read the same
// CODEX_HOME. A definition's model and effort can be passed to either as `-c model=...` and
// `-c model_reasoning_effort=...` (both document -c). If the TUI sends a model in its thread/start
// from its own config, an app-server override loses; if it sends none, the app-server's config
// decides. The candidates disagree on one observable, the thread the TUI's session runs on, so this
// makes them disagree:
//
//   CODEX_HOME/config.toml   model = "cfg-model",  effort "medium"
//   app-server               -c model="srv-model", -c model_reasoning_effort="low"
//   TUI                      `codex --remote`, through a proxy that logs every JSON-RPC frame
//
// Then a second run passes `-c` to the TUI instead (tui-model / "high"), and a third gives the TUI
// `-m flag-model`, the flag an operator types. Each run reports the TUI's thread/start (or resume)
// params and the app-server's answer: `model`, `reasoningEffort`.
//
// SEALED: a temporary CODEX_HOME with no credentials, loopback ports chosen by the OS, every process
// killed at the end. No aify-comms, aify-env or agent is involved. Run from the aify-comms checkout:
//   node docs/superpowers/plans/evidence/2026-10-01-p6/codex-model-probe.mjs

import { spawn } from "node:child_process";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../../../..");
const require = createRequire(path.join(ROOT, "mcp/stdio/package.json"));
const { WebSocketServer, WebSocket } = require("ws");
const pty = require("node-pty");

const freePort = () => new Promise((resolve) => {
  const server = net.createServer().listen(0, "127.0.0.1", () => { const { port } = server.address(); server.close(() => resolve(port)); });
});
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitForPort(port, ms = 15000) {
  const end = Date.now() + ms;
  while (Date.now() < end) {
    const ok = await new Promise((r) => { const s = net.connect(port, "127.0.0.1", () => { s.end(); r(true); }); s.on("error", () => r(false)); });
    if (ok) return true;
    await sleep(150);
  }
  return false;
}

async function run(label, { serverArgs, tuiArgs }) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "codex-model-probe-"));
  fs.writeFileSync(path.join(home, "config.toml"), 'model = "cfg-model"\nmodel_reasoning_effort = "medium"\n');
  const env = { ...process.env, CODEX_HOME: home };
  for (const key of Object.keys(env)) if (/^(AIFY_|CODEX_THREAD|CODEX_SESSION)/.test(key)) delete env[key];
  const [serverPort, proxyPort] = [await freePort(), await freePort()];
  const server = spawn("codex", [...serverArgs, "app-server", "--listen", `ws://127.0.0.1:${serverPort}`], { env, shell: process.platform === "win32", stdio: "ignore" });
  const frames = [];
  if (!await waitForPort(serverPort)) throw new Error(`${label}: the app-server never listened`);

  const proxy = new WebSocketServer({ host: "127.0.0.1", port: proxyPort });
  proxy.on("connection", (client) => {
    const upstream = new WebSocket(`ws://127.0.0.1:${serverPort}`);
    const pending = [];
    const accountReads = new Set();
    upstream.on("open", () => { for (const m of pending.splice(0)) upstream.send(m); });
    client.on("message", (data) => {
      const text = data.toString();
      frames.push({ dir: "tui->server", text });
      try { const msg = JSON.parse(text); if (msg.method === "account/read") accountReads.add(msg.id); } catch { /* not JSON */ }
      if (upstream.readyState === WebSocket.OPEN) upstream.send(text); else pending.push(text);
    });
    // THE ONE REWRITE: the sealed CODEX_HOME holds no credentials, so the TUI stops at its sign-in
    // screen before it starts a thread (first run of this probe). The account answer is replaced with
    // an API-key account; nothing else is altered, and no request reaches a model, because the probe
    // never sends a turn.
    upstream.on("message", (data) => {
      let text = data.toString();
      try {
        const msg = JSON.parse(text);
        if (accountReads.has(msg.id)) text = JSON.stringify({ ...msg, result: { account: { type: "apiKey" }, requiresOpenaiAuth: false } });
      } catch { /* not JSON */ }
      frames.push({ dir: "server->tui", text });
      client.send(text);
    });
    client.on("close", () => upstream.close());
    upstream.on("close", () => client.close());
  });

  const tui = pty.spawn(process.platform === "win32" ? "codex.cmd" : "codex", ["--remote", `ws://127.0.0.1:${proxyPort}`, ...tuiArgs],
    { name: "xterm-256color", cols: 120, rows: 40, cwd: home, env });
  let screen = "";
  tui.onData((d) => { screen += d; });
  await sleep(20000);

  try { tui.kill(); } catch { /* gone */ }
  proxy.close();
  if (process.platform === "win32") spawn("taskkill", ["/PID", String(server.pid), "/T", "/F"], { stdio: "ignore" });
  else server.kill("SIGKILL");
  await sleep(1500);
  fs.rmSync(home, { recursive: true, force: true });

  const parsed = frames.map((f) => { try { return { ...f, msg: JSON.parse(f.text) }; } catch { return null; } }).filter(Boolean);
  const methods = parsed.filter((f) => f.dir === "tui->server" && f.msg.method).map((f) => f.msg.method);
  const start = parsed.find((f) => f.dir === "tui->server" && /^thread\/(start|resume)$/.test(f.msg.method));
  const answer = start && parsed.find((f) => f.dir === "server->tui" && f.msg.id === start.msg.id);
  return {
    label,
    tuiMethods: [...new Set(methods)],
    threadRequest: start ? { method: start.msg.method, model: start.msg.params?.model ?? null, config: start.msg.params?.config ?? null } : null,
    threadAnswer: answer ? { model: answer.msg.result?.model ?? null, reasoningEffort: answer.msg.result?.reasoningEffort ?? null, error: answer.msg.error ?? null } : null,
    screenTail: screen.replace(/\x1b\[[0-9;?]*[A-Za-z]/g, "").replace(/\s+/g, " ").slice(-400),
  };
}

const results = [];
results.push(await run("A: -c on the app-server only", { serverArgs: ["-c", 'model="srv-model"', "-c", 'model_reasoning_effort="low"'], tuiArgs: [] }));
results.push(await run("B: -c on the TUI only", { serverArgs: [], tuiArgs: ["-c", 'model="tui-model"', "-c", 'model_reasoning_effort="high"'] }));
results.push(await run("C: -c on the app-server, -m on the TUI", { serverArgs: ["-c", 'model="srv-model"', "-c", 'model_reasoning_effort="low"'], tuiArgs: ["-m", "flag-model"] }));
console.log(JSON.stringify({ codex: "see `codex --version`", results }, null, 1));
process.exit(0);
