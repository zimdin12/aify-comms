// A hermes agent's reasoning effort, set on each live session its gateway hosts (P0 C9; review of P6, R1).
//
// hermes gives the path aify-comms runs no launch-time lever for effort. The agent's turns run in its
// gateway host (`hermes dashboard`), which reads effort from config.yaml, shared by every agent, or from
// the session itself; `--reasoning` reaches only a one-shot or the classic CLI, and the TUI has no
// variable for it (hermes 0.21.5 source, tui_gateway/server.py and hermes_cli/main_tui_launch.py).
// What the gateway does take is `config.set {key: "reasoning", value, session_id}`: scoped to that
// session and kept with it. A session id it does not know is refused, never written to the global config
// (tui_gateway/methods_config_set.py), and a sealed host was asked both
// (docs/superpowers/plans/evidence/2026-10-01-p6/hermes-model-probe.mjs).
//
// So the delivery loop, which already reads the gateway's live session, sets the agent's effort on each
// live session it has not set before. ONCE PER SESSION: a level the operator then picks inside it
// (`/reasoning`) is theirs and is not put back. A refusal is retried on the next pass and said once.
//
// THE SESSION IS THE ONE DELIVERY TARGETS, by delivery's own rule (`waitForActiveSession` in
// hermes-active-session.mjs): the session the agent's marker names by its live id, else the newest live
// session. The marker usually holds a session's DURABLE key (delivery and the resume sync write that),
// which names no live id, so delivery goes to the newest, and so does the effort. A rule of its own
// ("the marked session, else the only one, else none") set the effort nowhere while delivery used the
// newest (review of P6r2, H3), as newest-first had disagreed with an ephemeral marker before it (P6r,
// H3). Delivery's relaunch grace, which waits briefly before taking a newest session that predates the
// delivery, is not copied: the effort may reach that session too, once, and the new one on its own pass.
// ONE PASS AT A TIME, and nothing new is sent once stopped: a pass is a gateway round trip with a 60 s
// timeout against a 20 s interval, so overlapping passes set one session twice (H2), and a list answered
// after stop still set it (H4).
// The effort is the one hermes-aify resolved for this launch (AIFY_HERMES_SESSION_EFFORT): a managed
// launch's, else the agent's definition's.

import { buildSessionActiveListFrame, pickMostRecentSession, pickSessionById } from "./hermes-gateway-protocol.js";
import { readGatewayUrlMarker, readSessionIdMarker } from "./hermes-endpoint.js";
import { openGatewayWsClient } from "./hermes-gateway.mjs";
import { TMP_DIR } from "./hermes-env.mjs";

/** The frame that sets one session's reasoning effort. */
function buildSessionEffortFrame({ id, sessionId, effort }) {
  return {
    jsonrpc: "2.0",
    id,
    method: "config.set",
    params: { key: "reasoning", value: String(effort), session_id: String(sessionId) },
  };
}

/** The live session delivery targets: the marked one by its live id, else the newest; null when none is live. */
function deliveryTarget(activeList, marked) {
  return pickSessionById(activeList, marked) || pickMostRecentSession(activeList);
}

/**
 * Keep setting `effort` on the gateway's live session for `agentId`, once per session.
 * @returns {() => void} stop; a no-op when there is no agent or no effort to set
 */
export function startSessionEffort(opts = {}) {
  const {
    agentId,
    effort,
    intervalMs = 20_000,
    tempDir = TMP_DIR,
    openWs = openGatewayWsClient,
    readGatewayUrl = readGatewayUrlMarker,
    readMarker = readSessionIdMarker,
    nextId = (() => { let n = 1; return () => n++; })(),
    log = (msg) => console.error(msg),
  } = opts;
  const id = String(agentId || "").trim();
  const level = String(effort || "").trim();
  if (!id || !level || !Number.isFinite(intervalMs) || intervalMs <= 0) return () => {};
  const set = new Set();
  const said = new Set();
  let stopped = false;
  let passing = false;

  const tick = async () => {
    if (stopped || passing) return;
    const wsUrl = readGatewayUrl(id, { tempDir })?.gatewayUrl;
    if (!wsUrl) return;
    passing = true;
    let cli = null;
    try {
      cli = await openWs(wsUrl);
      const activeList = await cli.request(buildSessionActiveListFrame({ id: nextId(), currentSessionId: "" }));
      const sessionId = deliveryTarget(activeList, readMarker(id, { tempDir }));
      if (stopped || !sessionId || set.has(sessionId)) return;
      // A refusal rejects with the gateway's error object; the catch below says it.
      await cli.request(buildSessionEffortFrame({ id: nextId(), sessionId, effort: level }));
      set.add(sessionId);
      log(`[hermes-managed-host] reasoning effort ${level} set on session ${sessionId} of agent '${id}'.`);
    } catch (error) {
      const why = String(error?.message || error).replace(/token=[^&\s]*/g, "token=<redacted>");
      if (!said.has(why)) {
        said.add(why);
        log(`[hermes-managed-host] reasoning effort ${level} not yet set for agent '${id}': ${why}`);
      }
    } finally {
      passing = false;
      try { cli?.close?.(); } catch { /* ignore */ }
    }
  };

  const timer = setInterval(() => { tick().catch(() => {}); }, intervalMs);
  if (typeof timer.unref === "function") timer.unref();
  tick().catch(() => {});
  return () => { stopped = true; clearInterval(timer); };
}
