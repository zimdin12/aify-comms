// Plan 4 turn-busy heartbeat. While a runtime controller's start() promise
// is unresolved (mid-turn), POSTs turn_busy=1 to keep server-side status
// fresh independent of pre_llm_call / PostToolUse hook firing. Solves
// the operator-observed "working flapping to online during long turns"
// issue.
//
// File budget per 500-line rule: <=200 lines.

export function startTurnBusyHeartbeat({ agentId, intervalMs, isActive, postFn }) {
  const noop = () => {};
  if (!agentId || typeof isActive !== "function" || typeof postFn !== "function"
      || !Number.isFinite(intervalMs) || intervalMs <= 0) {
    return noop;
  }
  let stopped = false;

  const tick = async () => {
    if (stopped) return;
    let active = false;
    try { active = !!isActive(); } catch { return; }
    if (!active) return;
    try { await postFn(agentId); } catch { /* best-effort */ }
  };

  const timer = setInterval(tick, intervalMs);
  if (typeof timer.unref === "function") timer.unref();

  return () => {
    stopped = true;
    clearInterval(timer);
  };
}

// Default poster: POST /api/v1/agents/{id}/turn-start with body.source = "bridge-heartbeat"
export function makeDefaultTurnBusyPoster(baseUrl) {
  const root = String(baseUrl || "").replace(/\/+$/, "");
  return async (agentId) => {
    const url = `${root}/api/v1/agents/${encodeURIComponent(agentId)}/turn-start`;
    const res = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ source: "bridge-heartbeat" }),
    });
    if (!res.ok && res.status !== 404) {
      throw new Error(`turn-busy heartbeat ${res.status}`);
    }
  };
}
