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

// Returns a Set of port numbers already claimed by OTHER agents' persist files
// under tempDir. The current agent's own file is excluded so that the reuse
// branch can return its own previously-claimed port even when the gateway is
// already bound (and therefore not "free" by isPortFree). Unreadable or
// out-of-range entries are silently ignored.
function claimedByOtherAgents(tempDir, selfAgentId) {
  const ownFile = `aify-hermes-port-${sanitizeAgentId(selfAgentId)}`;
  const claimed = new Set();
  try {
    const entries = fs.readdirSync(tempDir);
    for (const name of entries) {
      if (!name.startsWith("aify-hermes-port-")) continue;
      if (name === ownFile) continue; // exclude self
      try {
        const val = parseInt(String(fs.readFileSync(path.join(tempDir, name), "utf8")).trim(), 10);
        if (Number.isInteger(val) && val >= PORT_BASE && val < PORT_BASE + PORT_SPAN) {
          claimed.add(val);
        }
      } catch {
        /* unreadable file → treat as no claim */
      }
    }
  } catch {
    /* tempDir missing or unlistable → no claims */
  }
  return claimed;
}

// Collision-resilient per-agent GATEWAY port (operator-reported 2026-05-31:
// `comms-senior-dev` and `graph-hermes-tl` both hashed to 9341, so the second
// agent's `hermes dashboard --tui --port 9341` could not bind → "gateway startup
// timeout"; worse, the idempotent reuse-probe could attach to the OTHER agent's
// gateway). agentPort() is a hash mod that CAN collide. Resolve a port that is
// stable per agent but collision-free:
//   1. If we already claimed a port (persisted file), reuse it — so ensure-host,
//      the delivery loop, and the visible TUI all agree on the SAME port.
//      NOTE: we do NOT require portFree here — the agent's own gateway may already
//      be bound to this port (that is the whole point of persistence). We only
//      skip the persisted port if another agent's file has claimed it (collision).
//   2. Else probe forward from agentPort() within the range for a port that is
//      both FREE (bindable) AND not claimed by any other agent's persist file,
//      then persist it.
// Persist file mirrors the per-agent key file convention.
export async function resolveGatewayPort(
  agentId,
  { tempDir = os.tmpdir(), portFree = isPortFree, probeSpan = 64 } = {},
) {
  const file = path.join(tempDir, `aify-hermes-port-${sanitizeAgentId(agentId)}`);
  const claimed = claimedByOtherAgents(tempDir, agentId);
  try {
    const existing = parseInt(String(fs.readFileSync(file, "utf8")).trim(), 10);
    if (Number.isInteger(existing) && existing >= PORT_BASE && existing < PORT_BASE + PORT_SPAN) {
      // Only reuse if no OTHER agent has claimed this port. We do not require
      // portFree — the agent's own gateway may already hold the port.
      if (!claimed.has(existing)) {
        return existing;
      }
      // Another agent claimed our persisted port → fall through and re-probe.
    }
  } catch {
    /* no persisted port → probe below */
  }
  const start = agentPort(agentId);
  let chosen = start;
  for (let i = 0; i < Math.max(1, probeSpan); i += 1) {
    const candidate = PORT_BASE + (((start - PORT_BASE) + i) % PORT_SPAN);
    // eslint-disable-next-line no-await-in-loop
    if ((await portFree(candidate)) && !claimed.has(candidate)) {
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

// Clear the per-agent GATEWAY markers — the `aify-hermes-port-<agent>` and
// `aify-hermes-key-<agent>` files. Best-effort, scoped to ONE agent, NEVER
// throws (missing files are fine).
//
// CALL ONLY ON A TERMINAL TEARDOWN — agent removed (410 from /dispatch/claim),
// explicit `stopDaemon`, or the delivery-loop's terminal-condition self-exit.
// Do NOT call on a transient gateway retry: the same agent reuses the SAME
// stable port across a restart, and dropping the port marker mid-restart would
// force a needless re-probe (and risk a different port). The marker writers
// (`resolveGatewayPort` / `loadOrCreateKey`) NEVER delete these today — this is
// the single owned deletion path (Task 4.1).
// Agent-keyed gateway-URL marker. The gateway host (`hermes dashboard --tui`)
// spawns the agent's MCP bridge with AIFY_HERMES_GATEWAY_URL still set to the
// literal `${AIFY_HERMES_GATEWAY_URL}` placeholder — the host cannot inject its
// own URL into the MCP child's env at spawn time (chicken-and-egg), so the
// bridge can't auto-register its gateway from env. ensure-host DOES know the
// wsUrl, so it writes THIS marker and the bridge reads it by AIFY_AGENT_ID.
// AGENT-keyed (not cwd-keyed like runtime-markers.js) so two hermes agents in
// the same folder never read each other's gateway. Overwritten on each
// ensure-host (the wsUrl/token rotate per launch); cleared on terminal teardown.
function gatewayUrlMarkerPath(agentId, tempDir) {
  return path.join(tempDir, `aify-hermes-gateway-${sanitizeAgentId(agentId)}`);
}

export function writeGatewayUrlMarker(agentId, gatewayUrl, { gatewayTokenEnv = "", tempDir = os.tmpdir() } = {}) {
  const safe = sanitizeAgentId(agentId);
  const url = String(gatewayUrl || "").trim();
  if (!safe || !/^wss?:\/\//i.test(url)) return false;
  try {
    fs.writeFileSync(
      gatewayUrlMarkerPath(agentId, tempDir),
      JSON.stringify({ gatewayUrl: url, gatewayTokenEnv: String(gatewayTokenEnv || "") }),
    );
    return true;
  } catch {
    return false; // best-effort: never throw
  }
}

export function readGatewayUrlMarker(agentId, { tempDir = os.tmpdir() } = {}) {
  const safe = sanitizeAgentId(agentId);
  if (!safe) return null;
  try {
    const raw = fs.readFileSync(gatewayUrlMarkerPath(agentId, tempDir), "utf8").trim();
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    const url = String(parsed?.gatewayUrl || "").trim();
    if (!/^wss?:\/\//i.test(url)) return null;
    return { gatewayUrl: url, gatewayTokenEnv: String(parsed?.gatewayTokenEnv || "") };
  } catch {
    return null;
  }
}

export function clearGatewayMarkers(agentId, dir = os.tmpdir()) {
  const safe = sanitizeAgentId(agentId);
  if (!safe) return;
  for (const name of [`aify-hermes-port-${safe}`, `aify-hermes-key-${safe}`, `aify-hermes-gateway-${safe}`]) {
    try {
      fs.rmSync(path.join(dir, name), { force: true });
    } catch {
      /* best-effort: never throw on teardown */
    }
  }
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
