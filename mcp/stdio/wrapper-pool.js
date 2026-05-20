// Bridge-side pool of long-lived per-agent runtime wrappers.
//
// Goal: a managed agent's wrapper (omp --mode rpc / codex app-server /
// opencode SDK / hermes runtime) stays alive across dispatches instead
// of being spawned fresh per /dispatch/claim. The bridge maintains a
// per-agent handle and routes each new dispatch through the existing
// handle's IPC. When a wrapper exits or fatal-errors, the entry is
// removed so the next dispatch spawns a fresh one.
//
// Each runtime adapter registers a `factory({ agentId, agentInfo, run,
// runtimeState, callbacks })` that produces a WrapperHandle:
//
//   {
//     capabilities,        // control capabilities (steer/interrupt/...)
//     dispatch(run, callbacks) => Promise<{status, summary, runtimeState,
//                                          externalRefs}>,
//     interrupt() => Promise<void>,
//     steer(text) => Promise<void>,
//     dispose() => Promise<void>,          // operator stop or fatal
//     alive() => boolean,
//   }
//
// The factory is responsible for the wrapper process and IPC. The pool
// only owns: identity (agentId), liveness tracking, lookup, and
// teardown coordination.

const POOL = new Map();
// Map<agentId, { runtime, handle, createdAt, lastUsedAt }>

function poolKey(agentId, runtime) {
  return `${agentId}::${String(runtime || "").toLowerCase()}`;
}

export function getWrapper(agentId, runtime) {
  const entry = POOL.get(poolKey(agentId, runtime));
  if (!entry) return null;
  if (entry.handle?.alive?.() === false) {
    POOL.delete(poolKey(agentId, runtime));
    return null;
  }
  return entry;
}

export async function ensureWrapper({ agentId, runtime, factory }) {
  const key = poolKey(agentId, runtime);
  const existing = POOL.get(key);
  if (existing && existing.handle?.alive?.() !== false) {
    existing.lastUsedAt = Date.now();
    return existing.handle;
  }
  if (existing) POOL.delete(key);
  if (typeof factory !== "function") {
    throw new Error(`wrapper-pool: factory required to create wrapper for ${agentId} (${runtime})`);
  }
  const handle = await factory();
  POOL.set(key, { runtime, handle, createdAt: Date.now(), lastUsedAt: Date.now() });
  // Best-effort auto-eviction on dispose. The handle's dispose() should
  // be idempotent and remove itself from the pool when called.
  if (handle && typeof handle.onExit === "function") {
    handle.onExit(() => {
      const current = POOL.get(key);
      if (current && current.handle === handle) POOL.delete(key);
    });
  }
  return handle;
}

export async function disposeWrapper(agentId, runtime) {
  const key = poolKey(agentId, runtime);
  const entry = POOL.get(key);
  if (!entry) return false;
  POOL.delete(key);
  try {
    await entry.handle?.dispose?.();
  } catch {
    // best effort
  }
  return true;
}

export function listPooledAgents() {
  return Array.from(POOL.entries()).map(([key, entry]) => ({
    key,
    runtime: entry.runtime,
    createdAt: entry.createdAt,
    lastUsedAt: entry.lastUsedAt,
    alive: entry.handle?.alive?.() !== false,
  }));
}

export async function disposeAll() {
  const keys = Array.from(POOL.keys());
  POOL.clear();
  await Promise.all(
    keys.map(async (key) => {
      const entry = POOL.get(key);
      try {
        await entry?.handle?.dispose?.();
      } catch {
        // best effort
      }
    }),
  );
}

// Test-only: drop all state without invoking dispose. Used by unit
// tests to reset the pool between cases.
export function _resetPoolForTests() {
  POOL.clear();
}
