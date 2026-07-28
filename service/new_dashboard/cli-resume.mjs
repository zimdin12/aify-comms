// "Continue in CLI" — the resume command shown in the agent drawer, and the REASON when there
// isn't one.
//
// Operator report 2026-07-28: "llama-manager does not have cli command that i can copy". Tracing it
// found this is not a missing runtime mapping — llama-manager's `sessionHandle` is EMPTY, so there
// is genuinely nothing to resume. Eleven agents on the live fleet are in that state.
//
// The defect was that the drawer rendered NOTHING in that case. An absent block is indistinguishable
// from a broken feature: the operator cannot tell "this agent has no session to resume yet" from
// "the dashboard failed to build the command". Same state-that-lies-by-omission class as the rest of
// this project's status work, so the block now always renders and says which it is.
//
// Extracted from app.js so the mapping is unit-testable; app.js itself is only reachable by
// source-regex tests, which cannot fail on wrong logic.

// Runtimes whose resident wrapper can resume a session by handle. Anything else must NOT get a
// fabricated command — a command that does not work is worse than an honest "not supported".
// `pi` is deliberately absent: install.sh disables the pi/omp resident wrapper on purpose
// ("presence-only and not installed by default because OMP has no multi-client resident wake
// surface"), so there is no `pi-aify` to hand the operator.
export const CLI_RESUME_RUNTIMES = new Set(['claude-code', 'hermes', 'codex']);

export function continueCliInfo(agent, session, { sessionRuntime = () => '', sessionAgentId = () => '' } = {}) {
  const handle = String(
    agent?.sessionHandle || agent?.session_handle || session?.sessionHandle || session?.session_handle || '',
  ).trim();
  const runtime = String(agent?.runtime || sessionRuntime(session) || '').trim().toLowerCase();
  const id = String(agent?.id || sessionAgentId(session) || '').trim();

  if (!handle) {
    return {
      command: '',
      reason: 'No pinned session handle yet, so there is nothing to resume. This agent gets one once '
        + 'it has run a session — send it a message, or start it, and the command appears here.',
    };
  }
  if (!CLI_RESUME_RUNTIMES.has(runtime)) {
    return {
      command: '',
      reason: `Resuming in your own terminal is not supported for the ${runtime || 'unknown'} runtime — `
        + 'there is no resident wrapper to hand you. Use the dashboard Console instead.',
    };
  }

  const agentFlag = id ? ` --aify-agent ${id}` : '';
  if (runtime === 'claude-code') {
    return { command: `claude-aify${agentFlag} --dangerously-skip-permissions --resume ${handle}`, reason: '' };
  }
  if (runtime === 'hermes') {
    return { command: `hermes-aify${agentFlag} --resume ${handle}`, reason: '' };
  }
  // codex: the wrapper now recovers the agent from a bare --resume, but keep the explicit env so a
  // copied command is self-contained and does not depend on that lookup succeeding.
  return {
    command: `AIFY_RUNTIME=codex AIFY_AGENT_ID=${id} AIFY_SESSION_HANDLE=${handle} CODEX_THREAD_ID=${handle}`
      + ` CODEX_HOME="$HOME/.local/state/aify-comms/managed-codex-home"`
      + ` codex --no-alt-screen resume --include-non-interactive ${handle}`,
    reason: '',
  };
}
