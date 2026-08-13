// Acquiring, finding and shutting down the pooled `PiSession` children.
//
// The pool is the CALLER of the session class, never the other way round: it constructs `PiSession`,
// decides when an existing child can be reused, and resolves the launcher and per-agent model before
// handing the session its start parameters. `pi-session.js` does not import this module, so the
// dependency runs strictly pool -> class -> registry.
//
// The registry Map is imported rather than declared here — see `pi-session-registry.mjs` for why the
// shared state cannot live in either of its two readers.

import { PiSession } from "./pi-session.js";
import { piSessionPool } from "./pi-session-registry.mjs";
import {
  terminateProcessTree,
  defaultPiCommand,
  runtimeLaunchAvailability,
  normalizePiModelOverride,
  getRuntimeConfig,
} from "./runtimes.js";

function resolvePiLauncher() {
  const availability = runtimeLaunchAvailability("pi");
  if (!availability.available) throw new Error(availability.message);
  return defaultPiCommand();
}

export async function acquirePiSession({ agentId, agentInfo, sessionId = "", cwd, onPoolEvent }) {
  const key = String(agentId || "").trim();
  if (!key) throw new Error("acquirePiSession requires an agentId");
  let session = piSessionPool.get(key);
  if (!session) {
    session = new PiSession({ agentId: key, agentInfo, sessionId, onPoolEvent });
    piSessionPool.set(key, session);
  } else {
    if (agentInfo) session.agentInfo = agentInfo;
    if (onPoolEvent) session._onPoolEvent = onPoolEvent;
  }
  const launcher = resolvePiLauncher();
  const config = getRuntimeConfig(agentInfo);
  const model = normalizePiModelOverride(agentInfo?.model || config.model || "");
  const thinking = String(config.thinking || config.effort || "").trim();
  await session.ensureStarted({
    launcher,
    cwd: cwd || agentInfo?.cwd || process.cwd(),
    model,
    thinking,
    sessionId,
    agentInfo,
  });
  return session;
}

export function getPiSession(agentId) {
  const key = String(agentId || "").trim();
  if (!key) return null;
  return piSessionPool.get(key) || null;
}

export async function shutdownAllPiSessions(reason = "shutdown") {
  const sessions = [...piSessionPool.values()];
  piSessionPool.clear();
  await Promise.all(sessions.map((s) => s.stop(reason).catch(() => {})));
}

export function __resetPiSessionPoolForTests() {
  for (const session of piSessionPool.values()) {
    try {
      if (session._proc) terminateProcessTree(session._proc);
    } catch {
      // swallow
    }
    session._proc = null;
    session._state = "dead";
    if (session._idleTimer) clearTimeout(session._idleTimer);
    if (session._startupTimer) clearTimeout(session._startupTimer);
    session._idleTimer = null;
    session._startupTimer = null;
    session._activeTurn = null;
    session._pendingCommandAcks.clear();
  }
  piSessionPool.clear();
}

export function __piSessionPoolSize() {
  return piSessionPool.size;
}

export function __piSessionPoolEntriesForTests() {
  return [...piSessionPool.values()];
}
