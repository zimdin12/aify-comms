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
