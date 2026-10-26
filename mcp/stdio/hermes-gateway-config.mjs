// Which hermes gateway this bridge talks to: `AIFY_HERMES_GATEWAY_URL`, the WebSocket URL a hermes agent is
// reachable on.
//
// WHERE IT COMES FROM (0.9.10): hermes starts this bridge as an MCP server of the agent's gateway, and hermes'
// config passes it `AIFY_HERMES_GATEWAY_URL: "${AIFY_HERMES_GATEWAY_URL}"` from the gateway's own environment. The
// launcher starts the gateway with that variable already set, because it mints the gateway's session token itself
// (aify-wrapper lib/hermes-gateway-start.mjs). Until 0.9.10 the gateway could not know its own URL, and comms wrote
// a marker file for this module to read instead.
//
// ONLY A REAL ws:// OR wss:// URL IS ACCEPTED. hermes's YAML `${VAR}` interpolation falls back to the LITERAL
// placeholder when the variable is not set in hermes's environment; on 2026-05-25 an agent had the literal
// `"${AIFY_HERMES_GATEWAY_URL}"` stored as its gatewayUrl and delivery was rejected. Anything else is absent.
//
// THIS MODULE RESOLVES AT IMPORT, and every reader needs the answer before it can act.
//
// DEPLOYMENT: host code. Inert until `install.sh` is re-run (sequentially) AND every wrapper relaunches.

const _rawHermesGatewayUrl = String(process.env.AIFY_HERMES_GATEWAY_URL || "").trim();
export const AIFY_HERMES_GATEWAY_URL = /^wss?:\/\//i.test(_rawHermesGatewayUrl) ? _rawHermesGatewayUrl : "";
if (_rawHermesGatewayUrl && !AIFY_HERMES_GATEWAY_URL) {
  console.error(`[aify] ignoring unresolved AIFY_HERMES_GATEWAY_URL placeholder: ${_rawHermesGatewayUrl.slice(0, 60)}. The agent's gateway was not started by hermes-aify; relaunch it through hermes-aify.`);
}
