// Small helpers used by server.js's comms_register tool handler.
// Extracted into a module so they can be unit-tested without spinning up
// the full MCP server.

export function fillSessionHandleFromAdapter(args, adapter) {
  if (!adapter) return args;
  // Native-session-id model (2026-06-03): hermes is treated like every other
  // runtime — the REAL session id wins, not a synthetic `aify-<agentId>` name.
  // (Reverts commit e89af02, which OVERRODE the hermes handle with the pinned
  // name.) Deliverability is keyed on the GATEWAY (resident-run / hermes-live
  // require a live ws:// gateway), NOT this handle, so a real id here never
  // falsely implies deliverable.
  const existing = String(args?.sessionHandle || "").trim();
  if (existing) return args;
  const detected = adapter.getCurrentSessionId();
  if (!detected) return args;
  return { ...args, sessionHandle: detected };
}
