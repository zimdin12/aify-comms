// Periodic re-read of the runtime adapter's current session id. When it
// changes, POST the new value to the aify-comms server via the existing
// PATCH /api/v2/agents/{agent_id}/session-handle endpoint. This is the
// canonical "report-back" path that lets the dashboard Console launch with
// --resume even after a fresh runtime spawn.

export function startSessionHandleHeartbeat({ adapter, agentId, intervalMs, postFn }) {
  const noop = () => {};
  if (!adapter || !agentId || typeof postFn !== "function" || !Number.isFinite(intervalMs) || intervalMs <= 0) {
    return noop;
  }
  let lastHandle = null;
  let stopped = false;

  const tick = async () => {
    if (stopped) return;
    let current = null;
    try { current = adapter.getCurrentSessionId(); } catch { /* swallow */ }
    // Plan 4: fall back to runtime-native discovery when env-read returns null.
    // Lets fresh managed launches capture session_handle via filesystem/RPC scan.
    if (!current && typeof adapter.discoverSessionId === "function") {
      try { current = await adapter.discoverSessionId(); } catch { /* swallow */ }
    }
    if (!current || current === lastHandle) return;
    try {
      await postFn(agentId, current);
      lastHandle = current;
    } catch {
      // best-effort — next tick will retry
    }
  };

  // Fire once immediately so first launch captures handle without waiting.
  tick();

  const timer = setInterval(tick, intervalMs);
  if (typeof timer.unref === "function") timer.unref();

  return () => {
    stopped = true;
    clearInterval(timer);
  };
}

// Default poster: PATCH /api/v2/agents/{id}/session-handle
export function makeDefaultHandlePoster(baseUrl) {
  const root = String(baseUrl || "").replace(/\/+$/, "");
  return async (agentId, sessionHandle) => {
    const url = `${root}/api/v2/agents/${encodeURIComponent(agentId)}/session-handle`;
    const res = await fetch(url, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sessionHandle, requestedBy: "bridge-heartbeat" }),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`session-handle PATCH ${res.status}: ${text.slice(0, 200)}`);
    }
  };
}
