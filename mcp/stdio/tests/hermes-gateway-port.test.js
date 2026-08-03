// Regression (operator-reported 2026-05-31): managed-hermes gateway ports are a
// hash mod (agentPort) that COLLIDES — comms-senior-dev and graph-hermes-tl both
// hash to 9341, so the second agent's `hermes dashboard --tui --port 9341` could
// not bind → "gateway startup timeout". resolveGatewayPort must give colliding
// agents distinct FREE ports and persist each agent's choice so ensure-host, the
// delivery loop, and the visible TUI all agree.
import { test } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { agentPort, resolveGatewayPort } from "../hermes-endpoint.js";
import { tmpDir } from "./_tmpdir.js";

const tmp = () => tmpDir("aify-gwport-");

test("colliding agents resolve to DIFFERENT free ports", async () => {
  const a = "comms-senior-dev";
  const b = "graph-hermes-tl";
  assert.equal(agentPort(a), agentPort(b), "precondition: these agentIds collide on agentPort");
  const dir = tmp();
  const taken = new Set();
  const portFree = async (p) => !taken.has(p);
  const pa = await resolveGatewayPort(a, { tempDir: dir, portFree });
  taken.add(pa); // a now holds its port
  const pb = await resolveGatewayPort(b, { tempDir: dir, portFree });
  assert.equal(pa, agentPort(a), "first agent keeps the base (hashed) port");
  assert.notEqual(pa, pb, "colliding second agent must probe forward to a different free port");
});

test("persists + reuses the same port across calls (ensure-host ↔ loop ↔ TUI agree)", async () => {
  const dir = tmp();
  const p1 = await resolveGatewayPort("agent-x", { tempDir: dir, portFree: async () => true });
  // Even if everything later looks 'taken', a persisted agent reuses ITS port.
  const p2 = await resolveGatewayPort("agent-x", { tempDir: dir, portFree: async () => false });
  assert.equal(p1, p2, "must reuse the persisted per-agent port");
});

test("stays within the documented port range", async () => {
  const dir = tmp();
  const p = await resolveGatewayPort("agent-y", { tempDir: dir, portFree: async () => false, probeSpan: 8 });
  assert.ok(p >= 8642 && p <= 9641, `expected 8642..9641, got ${p}`);
});

test("two agents sharing a base port get distinct ports even when both resolve before binding (nothing bound yet)", async () => {
  const a = "comms-senior-dev";
  const b = "graph-hermes-tl";
  assert.equal(agentPort(a), agentPort(b), "precondition: collide");
  const dir = tmp();
  // portFree always true => nothing is bound yet; only the claimed-set keeps them apart.
  const portFree = async () => true;
  const pa = await resolveGatewayPort(a, { tempDir: dir, portFree });
  const pb = await resolveGatewayPort(b, { tempDir: dir, portFree });
  assert.notEqual(pa, pb, "second agent must not reuse the first agent's claimed port");
});

test("a base port already claimed by ANOTHER agent's file is not handed out", async () => {
  const dir = tmp();
  const base = agentPort("mine");
  // Another agent already claimed this exact port.
  fs.writeFileSync(path.join(dir, "aify-hermes-port-other"), String(base));
  const p = await resolveGatewayPort("mine", { tempDir: dir, portFree: async () => true });
  assert.notEqual(p, base, "must skip a port claimed by another agent's file");
});
