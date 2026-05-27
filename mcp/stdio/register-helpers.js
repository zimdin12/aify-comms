// Small helpers used by server.js's comms_register tool handler.
// Extracted into a module so they can be unit-tested without spinning up
// the full MCP server.

export function fillSessionHandleFromAdapter(args, adapter) {
  if (!adapter) return args;
  const existing = String(args?.sessionHandle || "").trim();
  if (existing) return args;
  const runtime = String(adapter.name || "").trim().toLowerCase();
  const hermesGatewayUrl = String(process.env.AIFY_HERMES_GATEWAY_URL || "").trim();
  const explicitHermesHandle =
    String(process.env.AIFY_EXPLICIT_SESSION_HANDLE || "").trim() === "true" ||
    String(process.env.HERMES_EXPLICIT_SESSION_HANDLE || "").trim() === "true";
  if (runtime === "hermes" && /^wss?:\/\//i.test(hermesGatewayUrl) && !explicitHermesHandle) {
    return args;
  }
  const detected = adapter.getCurrentSessionId();
  if (!detected) return args;
  return { ...args, sessionHandle: detected };
}
