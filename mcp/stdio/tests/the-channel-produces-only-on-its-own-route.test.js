#!/usr/bin/env node
// Exactly one configured channel server produces in a Claude session, and the other has no effect at all.
//
// Claude starts the user-scope server (claude-channel.js) and the aify-channel plugin's (claude-channel-plugin.mjs)
// whenever both are configured, whatever the route admits (aify-wrapper docs/evidence/2026-10-22-channel-plugin, D-F).
// A server whose events Claude drops still claims the run and marks it delivered, and the message is lost. So each one
// decides from AIFY_CHANNEL_ROUTE (channel-producer.mjs).
//
// THE REAL ENTRIES, RUN AS CLAUDE RUNS THEM: each is spawned as a stdio MCP server against a recording stand-in for the
// service, with its own TEMP (the agent binding), XDG_STATE_HOME (the runtime marker) and HOME. What each one did is
// read from the service's log, the marker directory and its stdout, and each observable is named: the producer's
// liveness beat and its claim; the non-producer's request count and marker.

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { channelSource, DEV_ROUTE, isThisRoutesProducer, PLUGIN_CHANNEL_SOURCE, PLUGIN_ROUTE, USER_SCOPE_CHANNEL_SOURCE } from "../channel-producer.mjs";
import { sealedChildEnv } from "./_child-env.mjs";

const BRIDGE = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const USER_SCOPE = path.join(BRIDGE, "claude-channel.js");
const PLUGIN = path.join(BRIDGE, "claude-channel-plugin.mjs");
const AGENT = "route-witness";

/** A stand-in service: records every request, hands one channel run to the first claim. */
async function service() {
  const requests = [];
  let pending = { id: "run-1", messageId: "msg-1", executionMode: "channel", from: "tester", subject: "s", body: "b", priority: "normal" };
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      const parsed = body ? JSON.parse(body) : {};
      requests.push({ method: req.method, url: req.url, body: parsed });
      let answer = {};
      if (req.url === "/api/v1/dispatch/claim" && pending) { answer = { run: pending }; pending = null; }
      if (req.url === "/api/v1/dispatch/controls/claim") answer = { controls: [] };
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify(answer));
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { url: `http://127.0.0.1:${server.address().port}`, requests, close: () => new Promise((r) => server.close(r)) };
}

/** A sealed world: the binding names AGENT for children of this process, and nothing of this machine leaks in. */
function world() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-route-"));
  const [temp, state, home] = ["temp", "state", "home"].map((d) => path.join(dir, d));
  for (const d of [temp, state, home]) fs.mkdirSync(d, { recursive: true });
  fs.writeFileSync(path.join(temp, `aify-agent-${process.pid}`), JSON.stringify({ agentId: AGENT, pid: process.pid }));
  const markers = () => {
    const d = path.join(state, "aify-comms", "runtime-markers");
    return fs.existsSync(d) ? fs.readdirSync(d) : [];
  };
  return { dir, temp, state, home, markers };
}

/** A child's environment in this world: sealed from the live session, pointed at `url`, on `route` (unset when undefined). */
const worldEnv = (w, url, route) => sealedChildEnv({
  TEMP: w.temp, TMP: w.temp, XDG_STATE_HOME: w.state, HOME: w.home, USERPROFILE: w.home,
  AIFY_SERVER_URL: url, CLAUDE_MCP_SERVER_URL: url, AIFY_CLAIM_WAIT_MS: "0", AIFY_COMMS_CHANNEL_POLL_MS: "100",
  AIFY_CHANNEL_ROUTE: route,
});

/** Start one entry as Claude would, and speak MCP to it. */
function start(entry, w, url, route) {
  const child = spawn(process.execPath, [entry], { env: worldEnv(w, url, route), cwd: w.dir, stdio: ["pipe", "pipe", "pipe"] });
  const messages = [];
  let buffer = "";
  child.stdout.on("data", (chunk) => {
    buffer += chunk;
    let at;
    while ((at = buffer.indexOf("\n")) >= 0) {
      const line = buffer.slice(0, at); buffer = buffer.slice(at + 1);
      try { messages.push(JSON.parse(line)); } catch { /* not a message */ }
    }
  });
  const exited = new Promise((resolve) => child.on("exit", (code) => resolve(code)));
  const send = (m) => child.stdin.write(`${JSON.stringify(m)}\n`);
  send({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-06-18", capabilities: {}, clientInfo: { name: "witness", version: "0" } } });
  send({ jsonrpc: "2.0", method: "notifications/initialized" });
  return {
    child, messages, exited,
    initialize: () => messages.find((m) => m.id === 1)?.result,
    channelEvents: () => messages.filter((m) => m.method === "notifications/claude/channel"),
    stop: async () => { child.stdin.end(); const t = setTimeout(() => child.kill(), 5000); const code = await exited; clearTimeout(t); return code; },
  };
}

const settle = (ms) => new Promise((r) => setTimeout(r, ms));
/** The quiet window that shows a server did nothing: 15 of the producer's 100 ms poll periods. */
const QUIET_MS = 1500;
/** Wait for `condition`, bounded so a server that never gets there fails instead of hanging the suite. */
async function until(condition, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs;
  while (!condition() && Date.now() < deadline) await settle(50);
}

test("the producer decision: the plugin's server only on the plugin route, the user-scope server only on the development route", () => {
  for (const route of [undefined, "", "dev", "plugin", "PLUGIN", "DEV", "none", "x"]) {
    assert.equal(isThisRoutesProducer({ fromPlugin: true, route }), route === PLUGIN_ROUTE, `plugin, ${route}`);
    assert.equal(isThisRoutesProducer({ fromPlugin: false, route }), route === DEV_ROUTE, `user scope, ${route}`);
  }
  assert.equal(channelSource(true), PLUGIN_CHANNEL_SOURCE);
  assert.equal(channelSource(false), USER_SCOPE_CHANNEL_SOURCE);
});

for (const [name, entry, route, produces] of [
  // A plain `claude`: no route, though the world binds an agent, which is the identity a plain session can inherit.
  ["user-scope server, no route (a plain claude session)", USER_SCOPE, undefined, false],
  ["user-scope server, an unknown route", USER_SCOPE, "x", false],
  ["user-scope server, development route", USER_SCOPE, "dev", true],
  ["user-scope server, plugin route", USER_SCOPE, "plugin", false],
  ["plugin's server, plugin route", PLUGIN, "plugin", true],
  ["plugin's server, development route", PLUGIN, "dev", false],
  ["plugin's server, no route", PLUGIN, undefined, false],
]) {
  test(`${name}: ${produces ? "produces" : "has no effect"}`, async () => {
    const svc = await service();
    const w = world();
    const server = start(entry, w, svc.url, route);
    try {
      // A producer is done when its run is marked delivered; a non-producer, once it has answered and then stayed
      // quiet for 15 of the producer's poll periods.
      await until(() => server.initialize());
      if (produces) await until(() => svc.requests.some((r) => r.method === "PATCH"));
      else await settle(QUIET_MS);
      const init = server.initialize();
      assert.ok(init, `no initialize answer; stderr: ${server.child.stderr.read() ?? ""}`);
      if (produces) {
        const beat = svc.requests.find((r) => r.url === `/api/v1/agents/${AGENT}/heartbeat` && r.body.liveness === true);
        assert.ok(beat, "the producer's liveness beat never arrived");
        assert.ok(svc.requests.some((r) => r.url === "/api/v1/dispatch/claim"), "the producer never claimed");
        assert.equal(w.markers().length, 1, "the producer wrote no runtime marker");
        const events = server.channelEvents();
        assert.equal(events.length, 1, JSON.stringify(server.messages));
        assert.equal(events[0].params.meta.message_id, "msg-1");
        assert.ok(svc.requests.some((r) => r.method === "PATCH" && r.url === "/api/v1/dispatch/runs/run-1"), "the run was not marked delivered");
        assert.match(init.instructions, new RegExp(`source="${channelSource(entry === PLUGIN)}"`));
        assert.ok(init.capabilities?.experimental?.["claude/channel"], "the producer is not a channel");
      } else {
        assert.deepEqual(svc.requests, [], "the non-producer reached the service");
        assert.deepEqual(w.markers(), [], "the non-producer wrote a runtime marker");
        assert.deepEqual(server.channelEvents(), []);
        assert.equal(init.capabilities?.experimental?.["claude/channel"], undefined, "the non-producer offers a channel");
        assert.equal(init.instructions, undefined);
      }
      const code = await server.stop();
      if (!produces) assert.equal(code, 0, "the non-producer did not exit when Claude closed its stdin");
    } finally {
      server.child.kill();
      await server.exited;
      await svc.close();
      fs.rmSync(w.dir, { recursive: true, force: true });
    }
  });
}

test("an opted-in session that starts unbound produces once it registers", async () => {
  // The route activates the channel; the identity arrives later, as `comms_register` writes the binding. Activation
  // keyed on identity would leave this session deaf.
  const svc = await service();
  const w = world();
  const binding = path.join(w.temp, `aify-agent-${process.pid}`);
  const bound = fs.readFileSync(binding, "utf8");
  fs.rmSync(binding);
  const server = start(USER_SCOPE, w, svc.url, DEV_ROUTE);
  try {
    await until(() => server.initialize());
    assert.ok(server.initialize().capabilities?.experimental?.["claude/channel"], "the opted-in session is a channel from the start");
    await settle(QUIET_MS);
    assert.equal(svc.requests.filter((r) => r.url === "/api/v1/dispatch/claim").length, 0, "CONTROL: nothing is claimed while unbound");
    fs.writeFileSync(binding, bound);
    await until(() => svc.requests.some((r) => r.method === "PATCH"));
    assert.equal(server.channelEvents().length, 1, JSON.stringify(server.messages));
    assert.ok(svc.requests.some((r) => r.method === "PATCH" && r.url === "/api/v1/dispatch/runs/run-1"), "the run was not delivered after registering");
  } finally {
    server.child.kill();
    await server.exited;
    await svc.close();
    fs.rmSync(w.dir, { recursive: true, force: true });
  }
});

for (const route of ["plugin", "dev"]) {
  test(`both configured, ${route} route: one claimant, and the message reaches the session through it`, async () => {
    const svc = await service();
    const w = world();
    const userScope = start(USER_SCOPE, w, svc.url, route);
    const plugin = start(PLUGIN, w, svc.url, route);
    try {
      await until(() => userScope.initialize() && plugin.initialize() && svc.requests.some((r) => r.method === "PATCH"));
      await settle(QUIET_MS);
      const [producer, other] = route === "plugin" ? [plugin, userScope] : [userScope, plugin];
      assert.deepEqual(other.channelEvents(), []);
      const events = producer.channelEvents();
      assert.equal(events.length, 1, JSON.stringify(producer.messages));
      assert.equal(events[0].params.meta.message_id, "msg-1");
      assert.equal(w.markers().length, 1, `markers: ${w.markers()}`);
      const beats = svc.requests.filter((r) => r.body.liveness === true);
      assert.equal(beats.length, 1, "exactly one liveness beat: one producer");
    } finally {
      for (const s of [userScope, plugin]) { s.child.kill(); await s.exited; }
      await svc.close();
      fs.rmSync(w.dir, { recursive: true, force: true });
    }
  });
}

test("importing either entry starts nothing: no request, no marker, and the process ends by itself", async () => {
  const svc = await service();
  const w = world();
  try {
    for (const entry of [USER_SCOPE, PLUGIN]) {
      // On the route where this entry would produce, so only the import itself stands between it and its effects.
      const child = spawn(process.execPath, ["--input-type=module", "-e", `await import(${JSON.stringify(new URL(`file:///${entry.replace(/\\/g, "/")}`).href)});`],
        { env: worldEnv(w, svc.url, entry === PLUGIN ? PLUGIN_ROUTE : "dev"), stdio: ["pipe", "pipe", "pipe"] });
      const code = await Promise.race([new Promise((r) => child.on("exit", r)), settle(10_000).then(() => "still running")]);
      child.kill();
      assert.equal(code, 0, `${path.basename(entry)}: ${code}`);
    }
    assert.deepEqual(svc.requests, []);
    assert.deepEqual(w.markers(), []);
  } finally {
    await svc.close();
    fs.rmSync(w.dir, { recursive: true, force: true });
  }
});

test("the plugin's producer leaves when Claude does: the parent guard runs under the plugin's runChannel()", async () => {
  // A parent that starts the plugin's channel and exits at once: on Windows the child outlives it, so only the orphan
  // guard (the original parent, read twice, 3 s apart) ends it. The guard used to be armed only when this file was
  // claude-channel.js itself, which the plugin's entry never is.
  const svc = await service();
  const w = world();
  try {
    // The channel inherits the parent's sealed environment, on the plugin route.
    const parent = spawn(process.execPath, ["-e", [
      "const { spawn } = require('node:child_process');",
      `const c = spawn(process.execPath, [${JSON.stringify(PLUGIN)}], { detached: true, stdio: ['pipe', 'ignore', 'ignore'], cwd: ${JSON.stringify(w.dir)} });`,
      "process.stdout.write(String(c.pid)); c.unref(); process.exit(0);",
    ].join("\n")], { env: worldEnv(w, svc.url, PLUGIN_ROUTE) });
    let out = "";
    parent.stdout.on("data", (c) => { out += c; });
    await new Promise((r) => parent.on("exit", r));
    const pid = Number(out);
    assert.ok(pid > 0, `no child pid: ${out}`);
    const alive = () => { try { process.kill(pid, 0); return true; } catch { return false; } };
    const deadline = Date.now() + 20_000;
    while (alive() && Date.now() < deadline) await settle(500);
    const survived = alive();
    if (survived) process.kill(pid);
    assert.equal(survived, false, "the plugin's channel outlived its parent by 20 s");
  } finally {
    await svc.close();
    await settle(500);
    fs.rmSync(w.dir, { recursive: true, force: true });
  }
});

test("one declaration speaks for both entries: claude-channel.js declares the gate, and the plugin's entry runs its runChannel()", async () => {
  // aify-wrapper reads this line before choosing the plugin route. The plugin's entry carries none of its own: it imports
  // runChannel() from claude-channel.js, so it cannot run a gate other than the one declared there.
  const declared = (file) => fs.readFileSync(file, "utf8").match(/^export const CHANNEL_PRODUCER_GATE = (\d+);$/m)?.[1];
  assert.equal(declared(USER_SCOPE), "1");
  assert.equal((await import("../claude-channel.js")).CHANNEL_PRODUCER_GATE, 1);
  assert.equal(declared(PLUGIN), undefined);
  assert.match(fs.readFileSync(PLUGIN, "utf8"), /^import \{ runChannel \} from "\.\/claude-channel\.js";$/m);
});
