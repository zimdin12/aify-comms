// Which hermes gateway this bridge talks to, resolved from two sources in a fixed order.
//
// `AIFY_HERMES_GATEWAY_URL` is the live delivery variable — the WebSocket URL a resident hermes agent is
// reachable on. Twenty-one places in the bridge read it, and it had no owner. v0.5.4 layer 0 of the
// server.js decomposition, reviewer-cleared as the next unblocked owner.
//
// THE PRECEDENCE IS THE WHOLE POINT, and it exists because of a reported incident. hermes's YAML `${VAR}`
// interpolation falls back to the LITERAL placeholder string when the variable is not set in hermes's own
// environment. On 2026-05-25 an agent had the literal `"${AIFY_HERMES_GATEWAY_URL}"` stored as its
// gatewayUrl, its capability check failed, and delivery was rejected. So the env value is accepted ONLY if
// it is a real `ws://` or `wss://` URL, and anything else — placeholder, empty, an http URL — is treated as
// absent. Then, and only then, an agent-keyed marker file written by the gateway host is consulted.
//
// WHY THE FALLBACK IS NOT AN EDGE CASE: the gateway host spawns this MCP child with the variable STILL
// unexpanded, because it cannot inject its own URL into a child's environment at spawn time. So on every
// gateway-host launch the env is useless and the marker is the real source. Getting the order wrong does not
// degrade gracefully — it stores a placeholder as an agent's delivery address.
//
// `AIFY_HERMES_GATEWAY_TOKEN_ENV_FROM_MARKER` HOLDS THE NAME OF AN ENVIRONMENT VARIABLE, NOT A TOKEN. The
// marker records which variable the gateway's auth token lives in; the token itself is never read here,
// never stored here, and never logged. The constant's name invites the opposite reading, which is exactly
// why this paragraph exists.
//
// THIS MODULE RESOLVES AT IMPORT, and that is deliberate rather than an oversight. Reading the marker is a
// file read, so unlike `local-store.mjs` — which owns paths and refuses to touch the filesystem on import —
// this one does I/O when loaded, because the resolution IS what it exists to do and every reader needs the
// answer before it can act. The tests therefore drive it through child processes with different environments
// and marker states, which is the only way to observe a load-time decision.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

import { readGatewayUrlMarker } from "./hermes-endpoint.js";
// Validate the env var: hermes's YAML ${VAR} interpolation falls back to the
// LITERAL placeholder string when the var isn't set in hermes's own env
// (tools/mcp_tool.py _interpolate_env_vars). We MUST NOT propagate a
// "${AIFY_HERMES_GATEWAY_URL}" literal into the agent's runtime_config —
// operator-reported 2026-05-25: sc-hermes-test-1 had that literal stored
// as gatewayUrl, capability check failed, ping-pong rejected.
const _rawHermesGatewayUrl = String(process.env.AIFY_HERMES_GATEWAY_URL || "").trim();
export let AIFY_HERMES_GATEWAY_URL = /^wss?:\/\//i.test(_rawHermesGatewayUrl) ? _rawHermesGatewayUrl : "";
export let AIFY_HERMES_GATEWAY_TOKEN_ENV_FROM_MARKER = "";
if (!AIFY_HERMES_GATEWAY_URL) {
  // The gateway host (`hermes dashboard --tui`) spawns THIS MCP child with
  // AIFY_HERMES_GATEWAY_URL still the literal "${AIFY_HERMES_GATEWAY_URL}" — it
  // can't inject its own URL into the child env at spawn time — so env is
  // empty/placeholder here on EVERY gateway-host launch. Fall back to the
  // agent-keyed marker that managed-host.js ensure-host wrote with the real
  // wsUrl, so auto-registration captures the gateway without the agent having
  // to hand-roll its own MCP client (the prior failure mode).
  const _gwAgentId = String(process.env.AIFY_AGENT_ID || "").trim();
  if (_gwAgentId && !/^\$\{.*\}$/.test(_gwAgentId)) {
    const _gwMarker = readGatewayUrlMarker(_gwAgentId);
    if (_gwMarker?.gatewayUrl) {
      AIFY_HERMES_GATEWAY_URL = _gwMarker.gatewayUrl;
      AIFY_HERMES_GATEWAY_TOKEN_ENV_FROM_MARKER = _gwMarker.gatewayTokenEnv || "";
      console.error(`[aify] resolved hermes gatewayUrl from agent marker for '${_gwAgentId}' (env was ${_rawHermesGatewayUrl ? "an unresolved placeholder" : "unset"})`);
    }
  }
}
if (_rawHermesGatewayUrl && !AIFY_HERMES_GATEWAY_URL) {
  console.error(`[aify] ignoring unresolved AIFY_HERMES_GATEWAY_URL placeholder: ${_rawHermesGatewayUrl.slice(0, 60)} and no agent gateway marker found. Relaunch hermes-aify so the gateway host writes the marker before MCP child spawn.`);
}
