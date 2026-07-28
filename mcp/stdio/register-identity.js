// Does the session calling comms_register actually have the identity its hooks need?
//
// Operator question 2026-07-28: "about that missing cli.. why did it happen. does our skills have
// gap. it was fresh resident who read skills and then registered."
//
// It does, and the gap is structural rather than a typo in a doc. Every claude/codex turn hook
// installed by install.sh is gated on `AIFY_AGENT_ID`:
//
//     if [ -n "${AIFY_AGENT_ID:-}" ] && [ -n "${AIFY_COMMS_URL:-}" ]; then ... fi
//
// That variable is exported by the `*-aify` WRAPPER at launch. Environment variables cannot be
// injected into an already-running process, so a session that starts as a plain `claude` and only
// then calls `comms_register` can NEVER acquire it. The result is an agent that registered
// successfully and is structurally broken:
//
//   * SessionStart never binds a session handle -> `sessionHandle` stays empty -> no "Continue in
//     CLI" command (the llama-manager symptom: registered, resident, handle "");
//   * UserPromptSubmit/Stop never fire -> no turn-start/turn-end -> the agent's status LATCHES,
//     and nothing alive can clear it. claude-aify's own comment records what that costs: "days of
//     general-manager is always working".
//
// Registration reported success, so nothing told the agent any of this. That is the same
// state-that-lies class as the rest of this project's status work, and it is worth catching at the
// one moment the agent is paying attention.
//
// The BRIDGE can detect it even though the server cannot: the bridge is a child of the session, so
// `process.env.AIFY_AGENT_ID` is exactly the identity the hooks will use (or its absence).

// Managed sessions are excluded: their identity comes from the spawner, the bridge always carries
// it, and their turn signals come from the runtime host rather than shell hooks.
export function residentIdentityWarning({
  registeredAgentId,
  envAgentId,
  sessionMode,
  runtime,
} = {}) {
  const mode = String(sessionMode || '').trim().toLowerCase();
  if (mode && mode !== 'resident') return '';

  const wanted = String(registeredAgentId || '').trim();
  const have = String(envAgentId || '').trim();
  if (!wanted) return '';

  const wrapper = wrapperFor(runtime);

  if (!have) {
    return ` WARNING — this session has no AIFY_AGENT_ID, so it cannot report turns for "${wanted}":`
      + ` the turn hooks are gated on that variable and it can only be set when the session LAUNCHES.`
      + ` Consequences: no session handle is captured (so there is no "Continue in CLI" command), and`
      + ` this agent's status will latch because nothing will report turn-start/turn-end.`
      + ` Registration itself worked. To make it fully live, relaunch with`
      + ` \`${wrapper} --aify-agent ${wanted}\` and register again from that session.`;
  }

  if (have !== wanted) {
    return ` WARNING — this session's AIFY_AGENT_ID is "${have}", not "${wanted}". Turn and status`
      + ` signals from this terminal are reported for "${have}", so "${wanted}" will receive none and`
      + ` its status will latch. Register "${wanted}" from a session launched with`
      + ` \`${wrapper} --aify-agent ${wanted}\`, or register this session as "${have}".`;
  }

  return '';
}

function wrapperFor(runtime) {
  const r = String(runtime || '').trim().toLowerCase();
  if (r === 'codex') return 'codex-aify';
  if (r === 'hermes') return 'hermes-aify';
  if (r === 'claude-code' || r === 'claude') return 'claude-aify';
  return `${r || 'claude'}-aify`;
}
