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

// N11 (bug-hunt 2026-07-31): a codex session's rollout lives in a DIFFERENT store depending on how
// it was launched, and this file used to hardcode the managed one for every codex agent.
//   * managed  → `prepareCodexHome()` (runtimes-codex.js) → ~/.local/state/aify-comms/managed-codex-home
//   * resident → the codex-aify wrapper's `${CODEX_HOME:-$HOME/.codex}` (install.sh)
// Measured on the live fleet: 3 of the 4 codex agents holding a handle are RESIDENT, so three
// quarters of the rendered commands pointed `codex resume` at a store that cannot hold their
// rollout — the managed home had 0 rollouts on that host while ~/.codex had 19. Overriding
// CODEX_HOME for a resident session is worse than omitting it: omitting it lets the wrapper's own
// default apply, which is by construction the store the session was written to.
export const MANAGED_CODEX_HOME = '$HOME/.local/state/aify-comms/managed-codex-home';

import { sessionAgentId, sessionRuntime } from './record-fields.mjs';

export function continueCliInfo(agent, session, { sessionRuntime = () => '', sessionAgentId = () => '' } = {}) {
  const handle = String(
    agent?.sessionHandle || agent?.session_handle || session?.sessionHandle || session?.session_handle || '',
  ).trim();
  const runtime = String(agent?.runtime || sessionRuntime(session) || '').trim().toLowerCase();
  const id = String(agent?.id || sessionAgentId(session) || '').trim();
  // N10: the machine the session actually lives on. A resume command is only meaningful THERE —
  // the handle names a rollout/transcript file in that host's filesystem.
  const machine = String(agent?.machineId || agent?.machine_id || session?.machineId || session?.machine_id || '').trim();
  const mode = String(agent?.sessionMode || agent?.session_mode || '').trim().toLowerCase();

  if (!handle) {
    return {
      command: '',
      machine,
      reason: 'No pinned session handle yet, so there is nothing to resume. This agent gets one once '
        + 'it has run a session — send it a message, or start it, and the command appears here.',
    };
  }
  if (!CLI_RESUME_RUNTIMES.has(runtime)) {
    return {
      command: '',
      machine,
      reason: `Resuming in your own terminal is not supported for the ${runtime || 'unknown'} runtime — `
        + 'there is no resident wrapper to hand you. Use the dashboard Console instead.',
    };
  }

  const agentFlag = id ? ` --aify-agent ${id}` : '';
  let command;
  if (runtime === 'claude-code') {
    command = `claude-aify${agentFlag} --dangerously-skip-permissions --resume ${handle}`;
  } else if (runtime === 'hermes') {
    command = `hermes-aify${agentFlag} --resume ${handle}`;
  } else {
    // codex: the wrapper now recovers the agent from a bare --resume, but keep the explicit env so a
    // copied command is self-contained and does not depend on that lookup succeeding. CODEX_HOME is
    // set ONLY for managed sessions — see MANAGED_CODEX_HOME above.
    const codexHome = mode === 'managed' ? ` CODEX_HOME="${MANAGED_CODEX_HOME}"` : '';
    command = `AIFY_RUNTIME=codex AIFY_AGENT_ID=${id} AIFY_SESSION_HANDLE=${handle} CODEX_THREAD_ID=${handle}`
      + codexHome
      + ` codex --no-alt-screen resume --include-non-interactive ${handle}`;
  }

  return { command, machine, reason: '' };
}

// N10 (bug-hunt 2026-07-31): the command above is MACHINE-SPECIFIC and used to be rendered bare, as
// if it would work wherever it was pasted. It will not: `--resume <handle>` names a session file on
// one host's disk. Measured on the live fleet, 8 of the 28 agents rendering a command were for a
// session on another machine or another filesystem namespace (`linux:laputa` is a different physical
// host; `wsl-ubuntu:*` is the same box with a different $HOME and therefore a different session
// store) — and for the codex ones the rollout was verifiably absent from every home on the host
// where the command was displayed.
//
// The dashboard cannot know which machine the operator's terminal is on, so it must not GUESS and
// must not silently imply "here". Naming the machine is the one answer that is true wherever the
// command is pasted — into a terminal, a doc, or a chat message to a teammate.
//
// This is the same class as the defect this file was created to fix (a surface that lies by
// omission), reintroduced in the fix itself: an absent block at least prompted the operator to ask,
// while a confident wrong command does not.
export function resumeMachineNote(machine) {
  return machine
    ? `Run this on ${machine} — the session lives in that host's filesystem, so it will not resume anywhere else.`
    : 'This agent has no recorded machine, so the host that owns this session is unknown — resume it '
      + 'where the agent was last running.';
}

// The DEFAULT binding of the injection above. `continueCliInfo` takes its two record readers as parameters
// so a test can supply its own; these two supply the real ones, which is what every caller in the dashboard
// actually wants. Keeping the bound form beside the injectable one means the seam stays open for tests
// while callers stop re-binding it at each call site.
export function continueCliDetails(agent, session) {
  return continueCliInfo(agent, session, { sessionRuntime, sessionAgentId });
}

export function continueCliCommand(agent, session) {
  return continueCliDetails(agent, session).command;
}
