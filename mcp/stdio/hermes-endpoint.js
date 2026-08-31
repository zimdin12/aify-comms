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

// EXPORTED so the gateway-orphan check applies THIS range rather than a copy of it. A second
// hand-written 8642 somewhere else agrees until one of them is changed, and the check's whole job is
// deciding which running gateways are ours.
export const PORT_BASE = 8642; // first port in the per-agent range
export const PORT_SPAN = 1000; // range is PORT_BASE .. PORT_BASE + PORT_SPAN - 1 (8642–9641)

// Single tmp-dir resolution shared by EVERY marker reader/writer (the wrapper's
// `node -e`, server.js register, and hermes-managed-host.js's loop) so they all
// agree on WHERE the markers live. The loop uses TEMP||TMP||os.tmpdir(); marker
// helpers must default to the SAME order, else on a host where $TEMP/$TMP differ
// from os.tmpdir() (Windows Git-Bash) the loop writes one dir and the wrapper
// reads another → resume silently fails. (Review fix 2026-06-03.)
function defaultMarkerTmpDir() {
  return process.env.TEMP || process.env.TMP || os.tmpdir();
}

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
// THE OWNER of the hermes filename sanitiser. `hermes-daemon.js` and `hermes-loop-ready.js`
// declared byte-identical copies until v0.5.4; this module imports nothing, so it cannot cycle,
// and it holds eight of the eleven call sites.
//
// NOT THE SAME FUNCTION as `sanitizeAgentId` in `claude-session-store.js`, which shares the name
// and does something different: it keeps dots and substitutes underscores, where this one folds
// runs of anything unsafe into a single dash and trims the ends. `agent.1` becomes `agent.1`
// there and `agent-1` here. Unifying them would repoint existing files on disk, which is a
// migration and not a refactor — so they stay separate and this says why.
export function sanitizeAgentId(agentId) {
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
  { tempDir = defaultMarkerTmpDir(), portFree = isPortFree, probeSpan = 64 } = {},
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

export function writeGatewayUrlMarker(agentId, gatewayUrl, { gatewayTokenEnv = "", tempDir = defaultMarkerTmpDir() } = {}) {
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

export function readGatewayUrlMarker(agentId, { tempDir = defaultMarkerTmpDir() } = {}) {
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

// Agent-keyed REAL hermes session id marker. The native-session-id model
// (2026-06-03): an aify hermes agent lives in a NORMAL hermes session (its own
// timestamp id), symmetric with claude (UUID) / codex (thread). This marker is
// the single source of truth binding agentId -> its real session id, so launch
// (resume the same session), the delivery loop (target it), and the bridge
// (register it) all agree WITHOUT a round-trip — replacing the synthetic
// `aify-<agentId>` session name. Written when the agent first binds (launch or
// comms_register); read on relaunch to resume the SAME session. Agent-keyed
// (never cwd-keyed), so same-folder agents never collide.
function sessionIdMarkerPath(agentId, tempDir) {
  return path.join(tempDir, `aify-hermes-session-${sanitizeAgentId(agentId)}`);
}

// A USABLE hermes session id is alphanumeric + `_` / `-` (e.g.
// `20260604_050351_21531a`, the synthetic `aify-<agentId>`, or a short hex id).
// Reject empty AND — critically — unexpanded shell/template placeholders like
// `${HERMES_SESSION_ID}` or `${AIFY_SESSION_HANDLE}`. Those leak in when a
// config.yaml MCP-env template (`HERMES_SESSION_ID: "${HERMES_SESSION_ID}"`) or a
// wrapper var is UNSET, and if written they POISON the agent→session binding so the
// next launch resumes a nonexistent id and silently starts fresh (the 2026-06-04
// sc-tester incident). Guarding the marker read+write boundary makes a poison value
// a no-op regardless of which caller produced it (defense-in-depth).
export function isUsableSessionId(value) {
  const v = String(value || "").trim();
  return v.length > 0 && /^[A-Za-z0-9_-]+$/.test(v);
}

export function writeSessionIdMarker(agentId, sessionId, { tempDir = defaultMarkerTmpDir() } = {}) {
  const safe = sanitizeAgentId(agentId);
  const id = String(sessionId || "").trim();
  if (!safe || !isUsableSessionId(id)) return false; // never persist a placeholder/garbage id
  try {
    fs.writeFileSync(sessionIdMarkerPath(agentId, tempDir), id);
    return true;
  } catch {
    return false; // best-effort
  }
}

export function readSessionIdMarker(agentId, { tempDir = defaultMarkerTmpDir() } = {}) {
  const safe = sanitizeAgentId(agentId);
  if (!safe) return "";
  try {
    const v = fs.readFileSync(sessionIdMarkerPath(agentId, tempDir), "utf8").trim();
    // Treat a poisoned/placeholder marker (e.g. a pre-fix `${HERMES_SESSION_ID}`
    // written before the write-guard) as ABSENT, so the next launch falls through
    // to active_list resolution / fresh-start instead of resuming garbage.
    return isUsableSessionId(v) ? v : "";
  } catch {
    return "";
  }
}

// Clears the EPHEMERAL per-launch markers (port/key/gateway). These re-derive on
// the next launch, so it is safe (and correct) to call this on a relaunch reap
// (kill-prior -> stopDaemon) as well as terminal teardown. It deliberately does
// NOT touch the SESSION-id marker: that is the persistent agent->real-session
// binding the next launch must read to resume the SAME transcript — clearing it
// here was the 2026-06-03 regression that made every relaunch start fresh.
export function clearGatewayMarkers(agentId, dir = defaultMarkerTmpDir()) {
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

// Clears the PERSISTENT session-id binding. Call ONLY on a TERMINAL teardown —
// the agent was intentionally removed (410 from /dispatch/claim) — NOT on a
// relaunch reap, or the next launch loses its transcript and starts fresh.
export function clearSessionMarker(agentId, dir = defaultMarkerTmpDir()) {
  const safe = sanitizeAgentId(agentId);
  if (!safe) return;
  try {
    fs.rmSync(path.join(dir, `aify-hermes-session-${safe}`), { force: true });
  } catch {
    /* best-effort: never throw on teardown */
  }
}

// Resolve the per-agent endpoint. tempDir is injectable for tests; defaults to
// os.tmpdir() in production.
export function agentEndpoint(agentId, { tempDir = defaultMarkerTmpDir() } = {}) {
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
