#!/usr/bin/env node
// A Claude launch whose comms tools come over HTTP has no `server.js`, so its channel registers the agent, beats as
// its owner, and on the way out reports resident-lost and removes the binding it wrote (0.9.11 slice 3,
// resident-duties.mjs). A launch that still runs `server.js` leaves all of that to it.
//
// THE REAL ENTRY, RUN AS CLAUDE RUNS IT: claude-channel.js spawned as a stdio MCP server against a recording
// stand-in for the service, in a sealed TEMP and HOME. Leaving is Claude closing stdin. What arrived is read from
// the service's log and the TEMP directory, and each observable is named.

import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

import { ResidentDuties, holdsResidentDuties } from "../resident-duties.mjs";
import { sealedChildEnv } from "./_child-env.mjs";

const CHANNEL = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "claude-channel.js");
const AGENT = "residency-witness";

async function service(bindingPath) {
  const requests = [];
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      requests.push({ method: req.method, url: req.url, body: body ? JSON.parse(body) : {}, bound: fs.existsSync(bindingPath) });
      const answer = req.url === "/api/v1/dispatch/controls/claim" ? { controls: [] }
        : req.url === "/api/v1/agents" ? { ok: true, agentId: AGENT } : {};
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify(answer));
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  return { url: `http://127.0.0.1:${server.address().port}`, requests, close: () => new Promise((r) => server.close(r)) };
}

function world() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "aify-residency-"));
  const [temp, state, home] = ["temp", "state", "home"].map((d) => path.join(dir, d));
  for (const d of [temp, state, home]) fs.mkdirSync(d, { recursive: true });
  return { dir, temp, state, home, binding: () => path.join(temp, `aify-agent-${process.pid}`) };
}

async function until(condition, timeoutMs = 20_000) {
  const deadline = Date.now() + timeoutMs;
  while (!condition() && Date.now() < deadline) await new Promise((r) => setTimeout(r, 50));
}

/** One channel launch: started, given time to register, then closed the way Claude closes it. */
async function launch(commsMcp) {
  const w = world();
  const svc = await service(w.binding());
  // On a launch that still runs server.js, server.js registered and wrote this binding; the channel only reads it.
  if (!commsMcp) fs.writeFileSync(w.binding(), JSON.stringify({ agentId: AGENT, pid: process.pid }));
  const child = spawn(process.execPath, [CHANNEL], {
    env: sealedChildEnv({
      TEMP: w.temp, TMP: w.temp, XDG_STATE_HOME: w.state, HOME: w.home, USERPROFILE: w.home,
      AIFY_SERVER_URL: svc.url, CLAUDE_MCP_SERVER_URL: svc.url, AIFY_CLAIM_WAIT_MS: "0", AIFY_COMMS_CHANNEL_POLL_MS: "100",
      AIFY_AGENT_ID: AGENT, AIFY_RUNTIME: "claude-code", AIFY_SESSION_MODE: "resident", AIFY_COMMS_MCP: commsMcp,
    }),
    cwd: w.dir,
    stdio: ["pipe", "ignore", "pipe"],
  });
  let stderr = "";
  child.stderr.on("data", (c) => { stderr += c; });
  const exited = new Promise((resolve) => child.on("exit", (code) => resolve(code)));
  const registered = () => svc.requests.some((r) => r.url === "/api/v1/agents" && r.method === "POST");
  // A residency launch is ready once it registered and bound; the control, once the channel has beaten.
  await until(() => (commsMcp ? registered() && fs.existsSync(w.binding()) : svc.requests.length > 0));
  const boundWhileRunning = fs.existsSync(w.binding());
  const bindingWhileRunning = boundWhileRunning ? JSON.parse(fs.readFileSync(w.binding(), "utf8")) : null;
  child.stdin.end();
  const kill = setTimeout(() => child.kill(), 10_000);
  const code = await exited;
  clearTimeout(kill);
  await svc.close();
  const result = { requests: svc.requests, code, stderr, boundWhileRunning, bindingWhileRunning, boundAfter: fs.existsSync(w.binding()) };
  fs.rmSync(w.dir, { recursive: true, force: true });
  return result;
}

test("an http launch's channel registers, binds, beats as owner, and on stdin close reports lost and unbinds", async () => {
  const run = await launch("http");
  const registration = run.requests.find((r) => r.url === "/api/v1/agents" && r.method === "POST");
  assert.ok(registration, `no registration arrived; stderr: ${run.stderr}`);
  const owner = registration.body.bridgeId;
  assert.equal(registration.body.agentId, AGENT);
  assert.equal(registration.body.runtime, "claude-code");
  assert.equal(registration.body.sessionMode, "resident");
  assert.ok(run.boundWhileRunning, "the channel never wrote the binding file");
  assert.equal(run.bindingWhileRunning.startedAt, registration.body.bridgeStartedAt,
    "the binding does not carry this process's start, so a late answer to an older process could replace it");
  // Review of 9f6e1a90: anything written after a later request can land after a successor's registration.
  const readBack = run.requests.find((r) => r.method === "GET" && r.url === `/api/v1/agents/${AGENT}`);
  assert.equal(readBack?.bound, true, "the binding was not written before the registration's read-back");
  assert.deepEqual(run.requests.filter((r) => r.url.endsWith("/runtime-state")), [], "the route stores the owner; a later write can restore a retired one");
  const ownerBeat = run.requests.find((r) => r.url === `/api/v1/agents/${AGENT}/heartbeat` && r.body.bridgeKind === "resident");
  assert.equal(ownerBeat?.body.bridgeId, owner, "the owner beat does not carry the registration's id");
  const lost = run.requests.filter((r) => r.url === `/api/v1/agents/${AGENT}/resident-lost`);
  assert.equal(lost.length, 1, "resident-lost must arrive exactly once");
  assert.equal(lost[0].body.bridgeId, owner, "resident-lost does not carry the registration's id");
  const sidecarBeat = run.requests.find((r) => r.url === `/api/v1/agents/${AGENT}/heartbeat` && r.body.bridgeKind === "channel-sidecar");
  assert.notEqual(sidecarBeat?.body.bridgeId, owner, "the sidecar keeps its own delivery id");
  assert.equal(run.boundAfter, false, "the binding file outlived the channel");
  assert.equal(run.code, 0);
});

test("control: a launch still running server.js leaves registration and resident-lost to it", async () => {
  const run = await launch(undefined);
  assert.ok(run.requests.length > 0, "the channel never reached the service, so its silence below proves nothing");
  assert.equal(run.requests.filter((r) => r.url === "/api/v1/agents").length, 0);
  assert.equal(run.requests.filter((r) => r.url.endsWith("/resident-lost")).length, 0);
  assert.equal(run.code, 0);
});

test("the decision: only an http launch, and never an RPC child", () => {
  assert.equal(holdsResidentDuties({ AIFY_COMMS_MCP: "http" }), true);
  assert.equal(holdsResidentDuties({ AIFY_COMMS_MCP: "HTTP " }), true);
  assert.equal(holdsResidentDuties({}), false);
  assert.equal(holdsResidentDuties({ AIFY_COMMS_MCP: "http", AIFY_BRIDGE_DISABLED: "1" }), false);
});

test("leaving while the registration is still in flight reports the loss after it lands, within the one bound", async () => {
  // Review of 9f6e1a90: a loss reported first is ignored as bridge_not_current, then the registration makes a gone
  // process the owner.
  const order = [];
  let land;
  const duties = new ResidentDuties({
    agentId: AGENT, ownerId: "owner-1", env: { AIFY_SESSION_MODE: "resident" }, boundMs: 2000,
    call: async (method, url) => { order.push(url.endsWith("/resident-lost") ? "loss" : url); return {}; },
    register: () => new Promise((resolve) => { land = () => { order.push("registered"); resolve(); }; }),
    removeBinding: () => {},
  });
  duties.startBeats = () => [];  // the real beats would reach whatever service this process's environment names
  duties.start();
  const leaving = duties.end();
  await new Promise((r) => setTimeout(r, 50));
  assert.deepEqual(order, [], "the loss was reported while the registration was still in flight");
  land();
  await leaving;
  assert.deepEqual(order, ["registered", "loss"]);
});

test("leaving reports resident-lost only when this process owns the lifecycle; hermes-aify names its managed host", async () => {
  const sent = async (env) => {
    const calls = [];
    const duties = new ResidentDuties({
      agentId: AGENT, ownerId: "owner-1", env: { AIFY_SESSION_MODE: "resident", ...env },
      call: async (method, url) => { calls.push(url); return {}; }, register: async () => {}, removeBinding: () => {},
    });
    await duties.end();
    return calls.filter((url) => url.endsWith("/resident-lost")).length;
  };
  assert.equal(await sent({ AIFY_RESIDENT_LIFECYCLE_OWNER: "managed-host" }), 0);
  assert.equal(await sent({}), 1, "control: a Claude channel, which owns its lifecycle, reports");
});

test("leaving is bounded: a service that never answers holds the exit for the bound, and the binding still goes", async () => {
  const removed = [];
  const residency = new ResidentDuties({
    agentId: AGENT, ownerId: "owner-1", env: { AIFY_SESSION_MODE: "resident" },
    call: () => new Promise(() => {}), register: async () => {}, removeBinding: (args) => removed.push(args), boundMs: 50,
  });
  const started = Date.now();
  const hold = setTimeout(() => {}, 5000);  // the bound's own timer is unref'd, as the channel's exit should not wait on it
  await Promise.all([residency.end(), residency.end()]);
  clearTimeout(hold);
  assert.ok(Date.now() - started < 2000, "end() waited past its bound");
  assert.deepEqual(removed.map((r) => r.bridgeId), ["owner-1"], "the binding must be removed once, by its owner id");
});
