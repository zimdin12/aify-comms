# Plan: Pi/OMP persistent RPC + synthesized terminal + watchdog

Captured from the Plan-agent analysis on 2026-05-21. Source-of-truth for the multi-phase work the operator approved ("go"). Do NOT delete after the PR ships — keep as the architectural-decision narrative; promote the key bits into DECISIONS.md when implementing Phase 5.

## Goal (operator's words)

Replace today's per-dispatch `omp --mode rpc` spawn with a persistent child per pi agent that's reused across dispatches. Surface the child's activity as a synthesized "terminal" stream in the dashboard so the operator sees what the bridge sees. Provide dashboard input that becomes new RPC prompts. Apply to managed AND resident pi. Enforce single-OMP-process-per-session-id via a soft watchdog: notify "cannot unless you close xxx" — never kill.

## Why the symmetric "RPC into a visible TUI" path doesn't exist today

Three independent OMP-side sources confirmed:
- [docs/rpc.md](https://github.com/can1357/oh-my-pi/blob/main/docs/rpc.md) — RPC is stdio-only, modes mutually exclusive
- [README](https://github.com/can1357/oh-my-pi/blob/main/README.md) — no `--listen`/`--port`/`--server`/`--daemon` flags
- Upstream issue [#436 "oh-my-pi remote control"](https://github.com/can1357/oh-my-pi/issues/436) — exact feature filed, PR #446 stale since April 2026 and doesn't implement what we'd need anyway

So "synthesized stream in dashboard" is the closest UX we can deliver without an OMP feature shipping. Document the gap, link the upstream issue, ship a synthesized-stream UX.

## Phase 1 — Persistent RPC child per pi agent

**New file: `mcp/stdio/pi-session.js`**

- Exports `acquirePiSession(agentId, agentInfo, { onPoolEvent })`, `releasePiSession(agentId, reason)`, `shutdownAllPiSessions(reason)`, `PiSession` class for tests.
- Module-level `piSessionPool: Map<agentId, PiSession>`.
- Reuses `detectPiRuntimeFailure`, `extractPiSessionState`, `extractPiAssistantText`, `boundText`, `appendBounded`, `buildSystemPrompt`, `buildUserPrompt` from `runtimes.js` (don't move; other runtimes use some of them).

**`PiSession` class API:**

```
class PiSession {
  constructor({ agentId, agentInfo, idleTimeoutMs, onPoolEvent })
  get state()                                  // idle|starting|ready|busy|failing|dead
  get sessionId()
  async ensureStarted({ sessionId, resume })   // spawn child if needed, await ready+get_state
  async runTurn(run, callbacks)                // serializes; returns { promise, interrupt, steer }
  async interruptActive()                      // abort current turn; child stays alive
  async steerActive(text)
  async stop(reason)                           // graceful; terminateProcessTree as last resort
  _onChildExit(code, signal)
  _onStdoutLine(text)
  _routeEventToActiveTurn(event)
  _startIdleTimer() / _clearIdleTimer()
  _healAndRetryActiveTurn()                    // unresumable session recovery
}
```

**Lifecycle state machine:**

```
idle ──ensureStarted──▶ starting ──ready event──▶ ready
                            │                       │
                            ▼                       ├── runTurn ──▶ busy
                          failing                   │                 │
                            │                       │                 ├── agent_end ──▶ ready
                            ▼                       │                 ├── interrupt  ──▶ ready (cancelled)
                           dead ◀──child exit ──────┘─────────────────┘
                            │
                            ▼
                       (removed from pool; next acquire creates fresh PiSession)
```

**`runtimes.js::createPiController` (lines 2543–2969) shrinks to ~80 lines that:**

1. Short-circuits **resident** mode (`executionMode === "resident"`) to the legacy per-dispatch path. Rename existing body to `createPiControllerResidentLegacy` and keep it for now — Phase 4 watchdog handles the resident case.
2. Otherwise calls `acquirePiSession(...)`, then `session.runTurn(run, callbacks)`. Returns `{ capabilities, interrupt, steer, promise }` matching today's external shape so `dispatch-execution.js` callers don't change.

**`server.js::shutdownWithStatus` (around line 231):** add `await shutdownAllPiSessions("bridge exiting")` next to `TERMINAL_MANAGER.stopAll`.

**Configuration:** `runtimeConfig.piIdleTimeoutMs` (default 24h), env override `AIFY_PI_IDLE_TIMEOUT_MS`. Idle timer uses `.unref()` so it doesn't block bridge shutdown.

## Phase 2 — Synthesized terminal stream

- New virtual `terminal_session` row created when `PiSession` reaches `ready` state.
- Bridge captures each `AgentSessionEvent` (`message_update`, `tool_execution_start`, `tool_execution_end`, `RpcExtensionUIRequest`, `agent_start`, `agent_end`, `error`) and formats into human-readable terminal_output frames.
- Frames go through the existing `TERMINAL_OUTPUT_WRITES` coalescing queue so `outputSeq` stays monotonic and the WebGL renderer streams cleanly.
- Status flag on the agent row (`runtime_state.virtualTerminal: true`) distinguishes the synthesized stream from a real PTY for dashboard hints.

## Phase 3 — Dashboard input → RPC prompt

- `terminal-input` controls into the virtual terminal: buffer until `\r`, then `await session.runTurn({...synthetic run...})` via the PiSession's queue.
- Echo the typed line into the terminal output stream.
- Agent-initiated `RpcExtensionUIRequest` (select/confirm/input) renders as a prompt in the synthesized terminal; the operator's answer is captured the same way and sent back via the RPC response.

## Phase 4 — Watchdog (single-OMP-process-per-session-id)

- **omp-aify wrapper**: before `exec omp`, queries the bridge via HTTP (`/api/v1/agents/<id>/pi-session-state`). If bridge owns the session, refuses with: *"Agent 'X' is currently driven by aify-comms (visible in dashboard terminal). Stop it from the dashboard or use `omp-aify --standalone --aify-agent X` to launch a parallel session on a different session-id."*
- **Bridge side**: when operator's `omp-aify` registers as resident, the bridge stops its persistent RPC for that agent until the resident exits. When resident exits, bridge respawns its persistent RPC to drain queued dispatches.
- Mutex is soft: clear text, never `kill -9`.

## Phase 5 — Docs

- `DECISIONS.md`: new entry "Managed pi uses persistent RPC + synthesized terminal stream; resident pi uses watchdog mutex; OMP upstream gap #436".
- `install.pi.md`: rewrite Delivery path.
- `.claude/skills/aify-comms-debug/SKILL.md` + `.agents/` mirror: update the "managed pi has no visible terminal" entry.

## 13 implementation gotchas (from Plan agent — read carefully before coding)

1. **`settled` flag split** — per-controller today, must become per-turn (settled, promptAcked, finalText, finalSnapshotText, finalError, stderrText, interrupted, attemptTimer) vs. per-session (process alive, sessionId, healAttempted).
2. **`pendingCommandAcks` scoping** — tag each pending ack with `scope: "turn" | "session"`. On `agent_end`, reject only turn-scoped acks. `get_state` is session-scoped; `steer`/`abort` are turn-scoped.
3. **Startup timer must not fire mid-turn** — move startup detection to `ensureStarted`. Once ready, startup timer is dead; per-turn timeout uses its own attempt timer.
4. **`get_state` runs once on initial `ready`** — and again after a successful heal-respawn (sessionId changes). NOT on every turn.
5. **Interrupt semantics** — must send `{type: "abort"}` and NOT kill the process. Add ~5s abort grace timer; fall back to `stop()` + respawn-next-turn only if child doesn't yield.
6. **`buildArgs()` only on spawn** — model/thinking are baked in. If `agentInfo.model`/`thinking` changes between dispatches, detect via shallow comparison and `stop()`+respawn.
7. **`callbacks` are per-dispatch** — pool-level events (child crashed while idle) need a fallback `onPoolEvent` passed at acquire, routed to server.js logger.
8. **`AIFY_BRIDGE_DISABLED=1` + `AIFY_AGENT_ID=""` env** must be on **every** spawn including heal-respawn and idle-respawn. Centralize in `PiSession._spawnChild()`.
9. **Tests reuse the `fakeOmp` script across launches** — tests calling `launchRuntimeRun` expecting fresh spawns will hit pool reuse. Implement `__resetPiSessionPoolForTests()` exported helper, AND respawn-on-`runtimeState.sessionId`-mismatch in production code so it matches operator intuition.
10. **`argvCapturePath` accumulates across spawns** — existing tests already use `argvLines.at(-1)`. New "pool reuse" test must assert exactly **one** argv line for the second dispatch.
11. **`omp` may leak file handles or accumulate context over long lifetimes** — out of scope; `piIdleTimeoutMs` is the safety valve. Document the default 24h.
12. **`runtimeFailureText()` mixes finalError + stderrText** — scope per-turn but capture between-turn stderr to a small capped session buffer so auth/fatal classifier still fires.
13. **Steer rejection** — re-scope from `!proc || !proc.stdin?.writable || settled` to "no active turn". Match today's error string `"No active Pi turn to steer"` for compatibility.

## Tests to add

- Two sequential `launchRuntimeRun({agentId: "pi-worker"})` produce **one** argv line (proves reuse). Both results return same `sessionId`.
- Idle teardown: configure `piIdleTimeoutMs: 100`, run one dispatch, wait 200ms, third dispatch produces a new argv line.
- Crash recovery: extend fakeOmp to exit on a special body between turns; next dispatch respawns.
- Heal mid-active-turn: existing dead-session test still passes via the new in-session heal path.
- Bridge shutdown: `shutdownAllPiSessions("test")` and verify child exits.

## Status (as of save)

- [x] Phase 1 — Persistent RPC child (pi-session.js + createPiControllerManaged + pool tests)
- [x] Phase 2 — Synthesized terminal stream (formatPiEventAsTerminalFrame + sink/buffer in pi-session.js, /agents/{id}/virtual-terminal/ensure endpoint, bridge terminalSinkProvider; race-fix in PiSession.stop() pool-evicts synchronously before kill)
- [x] Phase 3 — Dashboard input routing (virtual-terminal-input.js buffer-and-dispatch manager; bridge routes terminal_controls with action=input/stop/resize to the persistent PiSession; dispatchVirtualTerminalLine drives runTurn off operator console keystrokes). Out-of-scope follow-on: RpcExtensionUIRequest reply path (needs OMP response-schema investigation).
- [x] Phase 4 — Watchdog (GET /agents/{id}/pi-session-state endpoint + omp-aify wrapper-side `bridgeOwned` check + `--standalone` override). Soft mutex via clear refusal text; bridge respawn-on-resident-exit handled implicitly because the persistent PiSession is only created on managed dispatches.
- [ ] Phase 5 — Docs

Update checkboxes as commits land. Reference: this conversation's source jsonl is at `~/.claude/projects/C--Docker-aify-comms/651b895f-a564-4d3a-8e0b-27f8429b1dd0.jsonl`.
