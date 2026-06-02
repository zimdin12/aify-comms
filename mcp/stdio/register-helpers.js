// Small helpers used by server.js's comms_register tool handler.
// Extracted into a module so they can be unit-tested without spinning up
// the full MCP server.

export function fillSessionHandleFromAdapter(args, adapter) {
  if (!adapter) return args;
  const runtime = String(adapter.name || "").trim().toLowerCase();
  // Hermes: the agent's session is ALWAYS the stable `aify-<agentId>` session
  // (what `--aify-agent` / the --resume recovery resumes). The bridge is the
  // authority — OVERRIDE any agent-guessed handle with the pinned session so
  // aify records the correct continue-able session uniformly with codex/claude,
  // and a confused agent can't register a wrong/stale one. Deliverability is
  // keyed on the GATEWAY (resident-run / hermes-live require a live ws:// gateway),
  // NOT this handle, so storing it never falsely implies deliverable. (A plain
  // launch with no agent id falls through to the env session id below.)
  if (runtime === "hermes") {
    const agentId = String(process.env.AIFY_AGENT_ID || "").trim();
    const pinned =
      String(process.env.HERMES_TUI_RESUME || "").trim() ||
      (agentId ? `aify-${agentId.replace(/[^a-zA-Z0-9_-]+/g, "-").replace(/^-+|-+$/g, "")}` : "");
    if (pinned) return { ...args, sessionHandle: pinned };
  }
  const existing = String(args?.sessionHandle || "").trim();
  if (existing) return args;
  const detected = adapter.getCurrentSessionId();
  if (!detected) return args;
  return { ...args, sessionHandle: detected };
}
