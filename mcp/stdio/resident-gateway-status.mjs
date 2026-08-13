// Reading a RESIDENT hermes agent's turn state from its own gateway — the arm gate, and the reader.
//
// A hermes agent's turn state cannot be inferred from the aify service, because the service's status is
// DERIVED from what this bridge reports. Asking it would be a feedback loop: the bridge would read back its
// own last answer and call it evidence. So the resident detector goes to the gateway instead and reads the
// session's OWN `["running"]` truth, which is the same thing `readManagedSessionStatus` does on the managed
// path in `hermes-managed-host.js`. Everything here exists to make that read safe to do on a poll loop.
//
// EVERY FAILURE READS AS `""`, AND THAT DIRECTION IS THE DESIGN. The detector treats `""` as "not idle", so
// a transient gateway hiccup can never end a turn early — it can only delay the end until the 1800s server
// backstop. The opposite bias would cut a live turn off mid-answer on one dropped socket.
//
// THE BACKOFF IS NOT AN OPTIMISATION. Once a gateway is sustained-dead — resident-lost, host gone — every
// poll tick would otherwise pay a full connect timeout, and the poll loop serves every agent this bridge
// has. After three consecutive failures it actually probes only one read in ten and returns `""` cheaply
// for the rest. Any successful request resets it to zero immediately, so a recovered gateway resumes within
// a single cycle rather than waiting out a window.
//
// THE WS CLIENT IS LAZY AND REUSED, and reopened on the next read after a close or error, so a dropped
// socket self-heals without anyone reconnecting it explicitly.
//
// BOTH SEAMS ARE INJECTABLE — `openWs` and `readSessionId` — which is what makes any of this testable
// without a gateway. The tests drive a fake client rather than describing the behaviour.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import { readSessionIdMarker } from "./hermes-endpoint.js";
import {
  buildSessionActiveListFrame,
  pickMostRecentSessionRow,
  pickSessionStatusById,
  pickSessionStatusForKey,
} from "./hermes-gateway-protocol.js";
import { openGatewayWsClient } from "./hermes-gateway.mjs";
import { pinnedSessionId } from "./hermes-session-id.js";

// RESIDENT-HERMES turn-END via the gateway detector (status-accuracy Task 1,
// 2026-06-07). The managed delivery loop runs startHermesGatewayTurnDetector
// against the gateway's session.active_list status and posts /turn-start /
// /turn-end — but a RESIDENT hermes ran NO such detector, so its turn never
// ended: up to 30 min of false `working` after every turn (the worst single
// status inaccuracy; pre_llm_call set turn-start, nothing cleared it, it
// self-healed only at the 1800s backstop). These two helpers wire the SAME
// detector into the resident bridge path.
//
// shouldArmResidentHermesTurnDetector — the arm gate. Only a hermes runtime with
// a non-empty ws://|wss:// gatewayUrl arms the detector (resident OR
// managed-resident); a non-hermes runtime or a missing/placeholder gateway is a
// hard no-op so it never opens a WS or posts a turn signal. Anti-feedback by
// construction: arming the detector can only ever SET working on a gateway
// "working" read and CLEAR on sustained idle — it never fabricates working.
export function shouldArmResidentHermesTurnDetector({ runtime, sessionMode, gatewayUrl } = {}) {
  void sessionMode; // accepted for symmetry with the managed path; gating is runtime+gateway only.
  if (String(runtime || "").trim() !== "hermes") return false;
  return /^wss?:\/\//i.test(String(gatewayUrl || "").trim());
}

// makeResidentGatewayStatusReader — the resident mirror of
// readManagedSessionStatus (hermes-managed-host.js): open the gateway WS,
// session.active_list, and resolve THIS agent's session status by its real id
// (pickSessionStatusById) → the legacy synthetic-key title match
// (pickSessionStatusForKey) → the most-recent row fallback (the gateway is
// PER-AGENT, so active_list holds only this agent's session). Best-effort:
// any WS / RPC error reads as "" — treated by the detector as not-idle, so a
// transient gateway hiccup NEVER ends a turn early (the 1800s server backstop
// still applies). The WS client is opened LAZILY and REUSED across reads;
// it is re-opened on the next read after a close/error so a dropped socket
// self-heals. ANTI-FEEDBACK-LOOP: this returns the gateway's OWN session
// ["running"] truth, never the aify server's derived status.
export function makeResidentGatewayStatusReader({
  agentId,
  gatewayUrl,
  openWs = openGatewayWsClient,
  readSessionId = (id) => {
    try { return String(readSessionIdMarker(id) || "").trim(); } catch { return ""; }
  },
} = {}) {
  const sessionKey = pinnedSessionId(agentId);
  let wsClient = null;
  let rpcId = 800000;
  // BACKOFF (2026-06-07): once the gateway is sustained-dead (resident-lost / host gone),
  // stop re-connecting every poll tick — each failed open eats a connect-timeout. After
  // FAIL_THRESHOLD consecutive failures, actually probe only 1 in BACKOFF_EVERY reads
  // (~10x fewer connects); the other reads return "" cheaply (a detector no-op). Any
  // successful request resets it instantly, so a recovered gateway resumes within one cycle.
  const FAIL_THRESHOLD = 3;
  const BACKOFF_EVERY = 10;
  let consecutiveFailures = 0;
  let skipCounter = 0;
  const wsOpen = (client) => {
    const rs = client?._socket?.readyState;
    return rs === undefined || rs === 1; /* OPEN (or a fake test client w/o a socket) */
  };
  return async () => {
    if (consecutiveFailures >= FAIL_THRESHOLD) {
      skipCounter = (skipCounter + 1) % BACKOFF_EVERY;
      if (skipCounter !== 0) return ""; // backed off — skip the connect attempt this tick
    }
    try {
      if (!wsClient || !wsOpen(wsClient)) {
        try { wsClient?.close?.(); } catch { /* ignore */ }
        wsClient = await openWs(gatewayUrl);
      }
      const listResp = await wsClient.request(
        buildSessionActiveListFrame({ id: rpcId++, currentSessionId: "" }),
      );
      consecutiveFailures = 0; // the gateway responded → clear any backoff
      const realId = readSessionId(agentId);
      if (realId) {
        const byId = pickSessionStatusById(listResp, realId);
        if (byId) return byId;
      }
      const byKey = pickSessionStatusForKey(listResp, sessionKey);
      if (byKey) return byKey;
      const recentRow = pickMostRecentSessionRow(listResp);
      return String(recentRow?.status || "").trim();
    } catch {
      // Gateway hiccup → drop the (possibly dead) client and read as "" so the
      // detector treats it as a transient no-op (never a false turn-end).
      try { wsClient?.close?.(); } catch { /* ignore */ }
      wsClient = null;
      consecutiveFailures += 1;
      return "";
    }
  };
}
