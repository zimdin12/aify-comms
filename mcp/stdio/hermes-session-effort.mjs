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
// The effort is the one hermes-aify resolved for this launch (AIFY_HERMES_SESSION_EFFORT): a managed
// launch's, else the agent's definition's.

import { buildSessionActiveListFrame, pickMostRecentSession } from "./hermes-gateway-protocol.js";
import { readGatewayUrlMarker } from "./hermes-endpoint.js";
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
    nextId = (() => { let n = 1; return () => n++; })(),
    log = (msg) => console.error(msg),
  } = opts;
  const id = String(agentId || "").trim();
  const level = String(effort || "").trim();
  if (!id || !level || !Number.isFinite(intervalMs) || intervalMs <= 0) return () => {};
  const set = new Set();
  const said = new Set();
  let stopped = false;

  const tick = async () => {
    if (stopped) return;
    const wsUrl = readGatewayUrl(id, { tempDir })?.gatewayUrl;
    if (!wsUrl) return;
    let cli = null;
    try {
      cli = await openWs(wsUrl);
      const sessionId = pickMostRecentSession(await cli.request(buildSessionActiveListFrame({ id: nextId(), currentSessionId: "" })));
      if (!sessionId || set.has(sessionId)) return;
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
      try { cli?.close?.(); } catch { /* ignore */ }
    }
  };

  const timer = setInterval(() => { tick().catch(() => {}); }, intervalMs);
  if (typeof timer.unref === "function") timer.unref();
  tick().catch(() => {});
  return () => { stopped = true; clearInterval(timer); };
}
