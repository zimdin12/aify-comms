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
    // Plan 6 A1 (2026-05-26): runtime discovery is authoritative.
    // env-read is fallback — operators leave stale env vars in their
    // shells (HERMES_SESSION_ID etc.), and the prior fallback order
    // (env first, discover only when env was null) pinned those stale
    // values in the server's stored handle indefinitely. Discover-first
    // is self-correcting; env-fallback preserves the legacy behavior
    // when the runtime can't be probed.
    if (typeof adapter.discoverSessionId === "function") {
      try { current = await adapter.discoverSessionId(); } catch { /* swallow; fall through */ }
    }
    if (!current) {
      try { current = adapter.getCurrentSessionId(); } catch { /* swallow */ }
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
export function makeDefaultHandlePoster(baseUrl, apiKey = "") {
  const root = String(baseUrl || "").replace(/\/+$/, "");
  const key = String(apiKey || "").trim();
  return async (agentId, sessionHandle) => {
    const url = `${root}/api/v2/agents/${encodeURIComponent(agentId)}/session-handle`;
    const headers = { "Content-Type": "application/json" };
    if (key) headers["X-API-Key"] = key;
    const res = await fetch(url, {
      method: "PATCH",
      headers,
      body: JSON.stringify({ sessionHandle, requestedBy: "bridge-heartbeat" }),
    });
    if (!res.ok) {
      const text = await res.text().catch(() => "");
      throw new Error(`session-handle PATCH ${res.status}: ${text.slice(0, 200)}`);
    }
  };
}
