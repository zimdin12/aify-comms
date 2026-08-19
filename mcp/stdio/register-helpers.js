// Small helpers used by server.js's comms_register tool handler.
// Extracted into a module so they can be unit-tested without spinning up
// the full MCP server.

export async function fillSessionHandleFromAdapter(args, adapter, opts = {}) {
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
  if (detected) return { ...args, sessionHandle: detected };

  // DISCOVERY FALLBACK (2026-08-19). `getCurrentSessionId()` reads env vars, and for claude that
  // is CLAUDE_SESSION_ID — a variable CLAUDE CODE NEVER SETS. So a resident claude agent that
  // registered by hand got no handle, the dashboard showed "No pinned session handle yet"
  // permanently, and re-registering could not help because this path asks the one question that
  // always returns nothing. Meanwhile the adapter's own `discoverSessionId()` finds it: capture
  // store (keyed by agent id), then env, then the freshest transcript in THIS agent's project dir.
  //
  // ORDER: env FIRST, discovery SECOND — deliberately the reverse of the session-handle heartbeat,
  // which discovers first. The heartbeat is a long-lived correcting loop, so discover-first lets it
  // walk away from a stale env value left in an operator's shell; a later tick fixes an early wrong
  // answer. Registration happens ONCE and its env-read path is what hermes, codex and pi are already
  // tested against, so here discovery may only ADD a handle where there was none. Making the two
  // consistent would be changing live registration for three runtimes to fix one — a test pins this.
  //
  // SCOPED BY THE ARGUMENTS BEING REGISTERED, not by ambient process state: the agent id keys the
  // capture store and the cwd bounds the transcript scan, so a bridge whose own cwd differs from the
  // agent's workspace still looks in the right place — and one agent can never adopt another's
  // session id, which is the contamination this adapter's precedence was written to defeat (#138).
  if (typeof adapter.discoverSessionId !== "function") return args;
  try {
    const discovered = await adapter.discoverSessionId({
      agentId: args?.agentId,
      cwd: args?.cwd,
      ...opts,
    });
    const normalized = String(discovered || "").trim();
    if (normalized) return { ...args, sessionHandle: normalized };
  } catch {
    // best-effort: a filesystem probe must never fail a registration. Without a handle the agent
    // registers exactly as it did before this fallback existed.
  }
  return args;
}
