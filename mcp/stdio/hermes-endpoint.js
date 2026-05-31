#!/usr/bin/env node
// Pure-ish derivation of a per-agent hermes api_server endpoint.
//
// ASYMMETRY(hermes): each hermes agent needs its OWN `hermes gateway run`
// daemon so the aify-comms MCP tools loaded into it carry that agent's
// AIFY_AGENT_ID (a single shared daemon = one process = one identity, which
// can't attribute comms_send to the right agent). agentEndpoint gives each
// agent a deterministic, collision-resistant {host,port,baseUrl} plus a stable
// per-agent api_server key.
//
//   - port  = 8642 + (FNV-1a(agentId) % 1000)  → range 8642–9641 inclusive.
//             Deterministic: same agentId → same port forever. No Math.random.
//   - key   = read from a per-agent key file under tempDir; if absent, generate
//             with crypto.randomBytes(24) (48 hex chars), persist (mode 0600
//             where supported), and return it. Stable across calls.
//
// Mirrors the pure-helper pattern of hermes-session-id.js.

import crypto from "node:crypto";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";

const PORT_BASE = 8642; // first port in the per-agent range
const PORT_SPAN = 1000; // range is PORT_BASE .. PORT_BASE + PORT_SPAN - 1 (8642–9641)

// FNV-1a 32-bit string hash — small, deterministic, no dependencies, no
// randomness. Good enough to spread agentIds across the port span.
function fnv1a(str) {
  let hash = 0x811c9dc5;
  const s = String(str);
  for (let i = 0; i < s.length; i += 1) {
    hash ^= s.charCodeAt(i);
    // hash *= 16777619, kept in unsigned 32-bit space.
    hash = Math.imul(hash, 0x01000193) >>> 0;
  }
  return hash >>> 0;
}

// Deterministic port for an agent, within the documented range 8642–9641.
export function agentPort(agentId) {
  return PORT_BASE + (fnv1a(agentId) % PORT_SPAN);
}

// Sanitize an agentId into a safe filename fragment (same charset rules as the
// pinned session id), so the key file name is a valid path component.
function sanitizeAgentId(agentId) {
  return String(agentId || "")
    .replace(/[^a-zA-Z0-9_-]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

// Is a local TCP port bindable (free) right now? Best-effort: tries to listen on
// 127.0.0.1:<port> and reports whether the bind succeeded.
export function isPortFree(port, host = "127.0.0.1") {
  return new Promise((resolve) => {
    const srv = net.createServer();
    srv.once("error", () => resolve(false));
    srv.once("listening", () => srv.close(() => resolve(true)));
    try {
      srv.listen(port, host);
    } catch {
      resolve(false);
    }
  });
}

// Collision-resilient per-agent GATEWAY port (operator-reported 2026-05-31:
// `comms-senior-dev` and `graph-hermes-tl` both hashed to 9341, so the second
// agent's `hermes dashboard --tui --port 9341` could not bind → "gateway startup
// timeout"; worse, the idempotent reuse-probe could attach to the OTHER agent's
// gateway). agentPort() is a hash mod that CAN collide. Resolve a port that is
// stable per agent but collision-free:
//   1. If we already claimed a port (persisted file), reuse it — so ensure-host,
//      the delivery loop, and the visible TUI all agree on the SAME port.
//   2. Else probe forward from agentPort() within the range for a FREE port
//      (never grabbing a port a colliding agent's gateway already holds) and
//      persist it.
// Persist file mirrors the per-agent key file convention.
export async function resolveGatewayPort(
  agentId,
  { tempDir = os.tmpdir(), portFree = isPortFree, probeSpan = 64 } = {},
) {
  const file = path.join(tempDir, `aify-hermes-port-${sanitizeAgentId(agentId)}`);
  try {
    const existing = parseInt(String(fs.readFileSync(file, "utf8")).trim(), 10);
    if (Number.isInteger(existing) && existing >= PORT_BASE && existing < PORT_BASE + PORT_SPAN) {
      return existing;
    }
  } catch {
    /* no persisted port → probe below */
  }
  const start = agentPort(agentId);
  let chosen = start;
  for (let i = 0; i < Math.max(1, probeSpan); i += 1) {
    const candidate = PORT_BASE + (((start - PORT_BASE) + i) % PORT_SPAN);
    // eslint-disable-next-line no-await-in-loop
    if (await portFree(candidate)) {
      chosen = candidate;
      break;
    }
  }
  try {
    fs.writeFileSync(file, String(chosen));
  } catch {
    /* best-effort persist; worst case we re-probe next time */
  }
  return chosen;
}

// Read (or generate + persist) the stable per-agent api_server key.
function loadOrCreateKey(agentId, tempDir) {
  const file = path.join(tempDir, `aify-hermes-key-${sanitizeAgentId(agentId)}`);
  try {
    const existing = fs.readFileSync(file, "utf8").trim();
    if (existing) return existing;
  } catch {
    /* missing/unreadable → generate below */
  }
  const key = crypto.randomBytes(24).toString("hex");
  // mode 0600 where supported (ignored on Windows); writeFileSync is atomic
  // enough for our per-agent single-writer use.
  fs.writeFileSync(file, key, { mode: 0o600 });
  try {
    fs.chmodSync(file, 0o600);
  } catch {
    /* chmod unsupported (Windows) → fine */
  }
  return key;
}

// Resolve the per-agent endpoint. tempDir is injectable for tests; defaults to
// os.tmpdir() in production.
export function agentEndpoint(agentId, { tempDir = os.tmpdir() } = {}) {
  const host = "127.0.0.1";
  const port = agentPort(agentId);
  const key = loadOrCreateKey(agentId, tempDir);
  return {
    host,
    port,
    baseUrl: `http://${host}:${port}`,
    key,
  };
}

export default agentEndpoint;
