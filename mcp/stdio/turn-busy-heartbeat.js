// Plan 4 turn-busy heartbeat. While a runtime controller's start() promise
// is unresolved (mid-turn), POSTs turn_busy=1 to keep server-side status
// fresh independent of pre_llm_call / PostToolUse hook firing. Solves
// the operator-observed "working flapping to online during long turns"
// issue.
//
// File budget per 500-line rule: <=200 lines.

//: How often a mid-turn bridge refreshes its own liveness, in milliseconds.
//:
//: NAMED HERE rather than left as a literal at the call site in server.js, because it has to stay
//: well under the server's `ACTIVE_RUN_BRIDGE_STALE_SECONDS` (api_core/live_process_probes.py) and
//: nothing could check that while it lived inside the bridge entrypoint -- importing server.js to
//: read a number would START a bridge. The comment below this one describes what happens when the
//: relationship breaks: a tool call longer than the server's window gets the live run reaped as a
//: dead bridge, mid-turn. `console-working-timing.test.js` now holds the two together.
export const TURN_BUSY_HEARTBEAT_MS = 30_000;

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
    // isActive may be sync OR async (e.g. an async transcript-mtime probe for
    // resident claude); `await` handles both — a non-promise is awaited transparently.
    try { active = !!(await isActive()); } catch { return; }
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

// Default poster: while a turn is active, refresh BOTH liveness timestamps the
// server tracks for an owning bridge:
//   1. POST /agents/{id}/turn-start -> agent_turn_state.turn_updated_at
//      (keeps status "working" during long turns).
//   2. POST /agents/{id}/heartbeat  -> bridge_instances.last_seen
//      (the owner-bridge lease the active-run staleness check reads).
//
// (2) is the fix for the busy-turn reap: /turn-start alone leaves
// bridge_instances.last_seen untouched, and resident liveness otherwise only
// refreshes at turn boundaries. So a tool call longer than the server's
// ACTIVE_RUN_BRIDGE_STALE_SECONDS made the server reap a live run as a dead
// bridge. The /heartbeat POST is liveness-only (no turnBusy field) and
// superseded-bridge-safe per the server contract. bridgeId is required for (2);
// when absent we keep the legacy turn-start-only behavior.
export function makeDefaultTurnBusyPoster(baseUrl, apiKey = "", bridgeId = "") {
  const root = String(baseUrl || "").replace(/\/+$/, "");
  const key = String(apiKey || "").trim();
  const bid = String(bridgeId || "").trim();
  return async (agentId) => {
    const encoded = encodeURIComponent(agentId);
    const headers = { "Content-Type": "application/json" };
    if (key) headers["X-API-Key"] = key;
    const turnRes = await fetch(`${root}/api/v1/agents/${encoded}/turn-start`, {
      method: "POST",
      headers,
      body: JSON.stringify({ source: "bridge-heartbeat" }),
    });
    if (!turnRes.ok && turnRes.status !== 404) {
      throw new Error(`turn-busy heartbeat ${turnRes.status}`);
    }
    if (bid) {
      const beatRes = await fetch(`${root}/api/v1/agents/${encoded}/heartbeat`, {
        method: "POST",
        headers,
        body: JSON.stringify({ bridgeId: bid }),
      });
      if (!beatRes.ok && beatRes.status !== 404) {
        throw new Error(`turn-busy liveness heartbeat ${beatRes.status}`);
      }
    }
  };
}
