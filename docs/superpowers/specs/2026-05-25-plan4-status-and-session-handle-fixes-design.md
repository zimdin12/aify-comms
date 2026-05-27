# Plan 4 — Status, Session-Handle, and Default-Path Fixes Design Spec

**Status:** Draft — pending operator review
**Author:** comms-senior-dev (Claude Opus 4.7) + operator
**Date:** 2026-05-25
**Related branch:** `feature/dashboard-console-mode`
**Builds on:** Plans 1+2+3 (RuntimeAdapter foundation + capabilities + controllers/delivery extraction)

## Goal

Close every operator-surfaced gap from live testing of Plans 1+2+3. Make wrapper-backed delivery the default, deprecate the synth-terminal fallback for runtimes with wrappers, fix session-handle capture for fresh managed launches, fix status taxonomy to stop lying, fix codex-aify's stale-handle path probe.

This is the "polish layer" that makes the Plans 1+2+3 architecture actually deployable for everyday use.

## Why

Operator-driven testing surfaced 12 distinct issues clustering into 5 themes. They span "the architecture is right but the defaults are wrong" (Phase A), "the architecture left a structural gap for fresh launches" (Phase B), "the architecture's status taxonomy lies" (Phase C), "the codex-aify probe checks the wrong path" (Phase D), and "wrapper/operator UX polish" (Phase E). Each is small individually; together they're the difference between "working in theory" and "deployable for daily use."

## Scope

### In scope (Plan 4)

**Phase A — Defaults flip + synth deprecation (issues 7, 12):**
- `DEFAULT_SETTINGS["managed_via_wrapper"] = ["codex", "hermes", "pi"]`
- `DEFAULT_SETTINGS["managed_pty_eager_spawn"] = True`
- Remove synth-terminal code paths (`aify://virtual-rpc/*`) for runtimes WITH wrappers (codex/hermes/pi/claude-code). Keep ONLY for opencode (no aify wrapper exists) as the fallback for runtimes without wrappers.
- Validation: existing `worker_idle_close_minutes` setting honored — idle wrapper PTYs reap per dashboard configuration.

**Phase B — Session-handle discovery for fresh managed launches (issues 8, 10):**
- New `discoverSessionId()` method on each adapter (both languages). Returns the runtime's current session id by reading runtime-native storage, NOT from env.
- Per-runtime implementations:
  - **pi:** read most-recent active session from `~/.omp/agent/sessions/` directory (or `agent.db` SQLite if cleaner). Implementer decides during task.
  - **codex:** read `~/.codex/sessions/` — handles candidate layouts (flat / date-sharded / dir-per-session — see Phase D's storage research).
  - **hermes:** query gateway's `session.most_recent` JSON-RPC method if `AIFY_HERMES_GATEWAY_URL` is live; otherwise read `~/.hermes/sessions/` filesystem fallback if present.
  - **claude:** parse `CLAUDE_SESSION_ID` from claude-aify's own env first; fallback to JSONL transcript freshest-mtime scan.
- Bridge heartbeat (`mcp/stdio/session-handle-heartbeat.js`) calls `adapter.discoverSessionId()` when env-read returns null. POSTs to `/api/v2/agents/{id}/session-handle` as before.
- Closes Issue 8 (Stop+Start launches fresh-not-resumed) as a corollary — once `session_handle` is captured, `_default_console_command` builds with `--resume`.

**Phase C — Status taxonomy fix (issues 4, 1, 3):**
- `_compute_agent_status` / `_refresh_agent_live_state` for managed agents must verify a live `terminal_session` row (wrapper-backed) OR a live RPC controller registration (managed-RPC fallback) before claiming `online`. Otherwise `available`.
- New bridge `working` heartbeat: while a controller's `start()` promise is unresolved, the bridge POSTs `turn_busy=1` every 30s. Independent of `pre_llm_call` / `PostToolUse` hook firing. Lives in `mcp/stdio/turn-busy-heartbeat.js` (new) or extends `session-handle-heartbeat.js`.
- New `ready` status: emitted by adapter's `controllerFor` instance when its `start()` has completed initial handshake. Taxonomy becomes `available → starting → ready → working → ready → ... → offline`. The `online` alias remains for backwards-compatibility (semantic: "process exists").
  - claude-code: `ready` when claude-channel.js confirms channel binding (sets `runtime_config.channelEnabled=true`)
  - codex: `ready` when app-server WS connection initialized
  - hermes: `ready` when gateway WS connected + first prompt.submit ack OR managed wrapper has its TUI on screen
  - pi: `ready` when omp's `agent_ready` RPC event fires
  - opencode: skipped (no live readiness signal beyond process-alive)

**Phase D — codex-aify stale-handle probe (issue 11):**
- Implementer-side recon: confirm codex's session storage layout (flat / date-sharded / dir-per-session). Document findings.
- Update `install.sh` codex-aify wrapper's path probe to accept the candidate layouts. Prefer using `codex sessions list` or equivalent CLI probe if codex offers one. Fallback to multi-path filesystem check.
- Same logic exported into `mcp/stdio/codex-controller.js` so the bridge probes identically.
- Document the storage layout in `install.codex.md` for future-proofing.
- Closes task #118 (codex CLI ordering validation).

**Phase E — Wrapper/operator UX (issues 2, 5, 6, 9):**
- **Issue 2 (claude-aify MCP isolation):** smoke-verify only. Launch fresh claude-aify, run `/mcp` or list `mcp__*` tools, confirm the operator's full `~/.claude.json` MCP list is reachable. Close issue or escalate.
- **Issue 5 (auto-deploy / wrapper-drift visibility):** add `./redeploy.sh` script that detects installed wrappers via `ls ~/.local/bin/*-aify`, re-runs `install.sh --client <X> $SERVER_URL` for each. Document in `README.md`.
- **Issue 6 (hermes-session-resume needs provider config):** drop `hermes-session-resume` wake-mode entirely. Phase B's `discoverSessionId` reliably captures `gatewayUrl` into `runtimeConfig` after hermes-aify starts → resident wake-mode is always `hermes-live` → messages inject into LIVE hermes via gateway → no spawn-fresh-hermes worker needed. Same structural cleanup pattern as Plan 2's pi flip.
- **Issue 9 (Console widget oscillation):** with synth-terminal deprecation in Phase A, oscillation mostly disappears for wrapper-backed runtimes. For opencode (still synth), keep `state.sessionTerminals` cache. Add explicit "prefer wrapper PTY when both exist for same agent" rule to `chooseSessionConsoleWidget` in `service/new_dashboard/app.js`.

**Phase F — Holistic review + finishing:**
- Dispatch code-reviewer subagent over full Plan 4 diff.
- Fix Critical/Important concerns surfaced.
- Update DECISIONS.md, README.md, install.*.md, skills (both `.claude/` and `.agents/` mirrors).
- `superpowers:finishing-a-development-branch`.

### Out of scope

- runtimes.js helpers extraction (#123 — separate plan)
- `service/routers/api_v2.py` (13000-line monolith — Plan 5 territory)
- Opencode multi-client wiring via `opencode serve` (separate follow-up)

## Architecture

### Adapter contract extension (Phase B)

**JS (`mcp/stdio/adapters/`):**
```js
class RuntimeAdapter {
  // ... existing Plans 1+2+3 methods ...

  // Plan 4: fresh-launch session-id discovery.
  // Returns the runtime's current session id by reading runtime-native
  // storage (NOT env). Used by heartbeat as a fallback when env read
  // returns null. Returns null if no discoverable session exists.
  async discoverSessionId() { throw new Error("abstract"); }
}
```

**Python (`service/runtimes/`):**
```python
class RuntimeAdapter:
    # ... existing Plans 1+2+3 methods ...

    async def discover_session_id(self) -> str | None:
        raise NotImplementedError("subclass must override discover_session_id")
```

Per-runtime overrides described in Phase B above.

### Heartbeat extension (Phase B + C)

`mcp/stdio/session-handle-heartbeat.js` extends to:
1. Read `adapter.getCurrentSessionId()` (env-based, existing)
2. If null, fall through to `await adapter.discoverSessionId()` (Plan 4 new)
3. POST whichever returns non-null to `/api/v2/agents/{id}/session-handle`

New `mcp/stdio/turn-busy-heartbeat.js` (separate file for separation of concerns, ≤200 lines):
1. Listens for `controller.start()` promise creation/resolution
2. While active: POSTs `turn_busy=1` to `/api/v1/agents/{id}/turn-start` every 30s
3. Stops when promise resolves

### Status resolver fix (Phase C)

`service/routers/api_v2.py:_compute_agent_status` for managed agents:

```python
# Plan 4: managed agents must have a live worker before claiming `online`.
if session_mode == "managed":
    live_terminal = await _has_live_terminal_session(db, agent_id)
    live_rpc_child = await _has_live_rpc_controller(agent_id)
    if not (live_terminal or live_rpc_child):
        return "available"
```

Where:
- `_has_live_terminal_session(db, agent_id)` queries `terminal_sessions` for a row with `status='running'` and `bridge_instance_id` matching a current environment.
- `_has_live_rpc_controller(agent_id)` checks the in-memory registration of active RPC controllers (for the synth-RPC fallback path).

### Status taxonomy extension (Phase C)

Add `ready` between `online` and `working`:

```
available → starting → ready → working → ready → ... → offline
                                    ↓
                                  blocked
```

`ready` is emitted by the bridge after the adapter's `controllerFor` instance's `start()` completes initial handshake. New endpoint: `PATCH /api/v2/agents/{id}/ready`. Server stores `agent_turn_state.ready=true`. `_compute_agent_status` returns `ready` when `live_terminal/rpc AND ready=true AND turn_busy=false`.

Dashboard color coding: `ready` = green, `online` = light green (process alive but not handshake-complete), `available` = grey, `working` = animated indicator.

### Synth-terminal deprecation (Phase A)

The `aify://virtual-rpc/<runtime>` code path stays only for:
- opencode (no aify wrapper)
- A hard failure path: if `managed_via_wrapper` is set but the wrapper fails to spawn (e.g., wrapper binary missing), fall back to synth so the operator can still receive messages

For codex/hermes/pi/claude-code with `managed_via_wrapper`, synth terminal creation is skipped — the wrapper PTY IS the terminal. `runtime_state.virtualTerminal=true` cleared. Dashboard widget chooser prefers wrapper PTY.

### Default settings change (Phase A)

`service/routers/api_v2.py:DEFAULT_SETTINGS`:

```python
DEFAULT_SETTINGS = {
    # ... existing ...
    "managed_via_wrapper": ["codex", "hermes", "pi"],  # was: False
    "managed_pty_eager_spawn": True,                    # was: False
    # ... existing ...
}
```

Existing operator settings in the DB are not affected (only DEFAULT). New installs (or `reset_settings`) pick up the new defaults.

## Failure modes & error handling

| Failure | Behavior |
|---|---|
| Adapter's `discoverSessionId()` raises | Heartbeat catches, logs once, falls back to env-read result. No retry storm. |
| Pi's session storage format changes upstream | Plan 4 implementer documents the assumption; future omp updates may need a path adapter. |
| Codex's session storage layout changes | Wrapper probe + bridge probe both updated; layout documented in install.codex.md. |
| `managed_via_wrapper=true` but wrapper binary missing | Fallback to synth-terminal RPC path (existing behavior); dashboard surfaces "wrapper missing" warning. |
| Turn-busy heartbeat POST fails | Best-effort — next tick retries. Same shape as Plan 1 session-handle heartbeat. |
| `ready` event never fires (handshake hangs) | Status stays `starting`. Operator-visible — they see the agent never went green. Surfaces hanging-init bugs. |

## Testing strategy

- **Per-adapter contract tests** for `discoverSessionId()` in both languages — mock the runtime storage, verify the method returns the expected session id.
- **Heartbeat integration test** — spawn bridge with mocked env empty, mocked discoverSessionId returning a value, verify POST fires.
- **Status resolver regression tests** — verify managed agent without terminal_session returns `available`, with terminal_session returns `online`/`ready`.
- **`ready` lifecycle test** — adapter's `start()` returns, ready endpoint fires, status transitions correctly.
- **Synth-terminal absence test** — managed codex/hermes/pi dispatch with `managed_via_wrapper` on → no synth row created.
- **Cross-language consistency test** extended with new methods.

## Rollout

1. Phase A (defaults flip + synth deprecation): largest behavioral change, ships first. Existing operator settings preserved.
2. Phase B (discoverSessionId per adapter): closes the session-handle gap.
3. Phase C (status taxonomy + working heartbeat + ready status): UX accuracy.
4. Phase D (codex stale-handle probe): codex polish.
5. Phase E (wrapper/UX issues): low-risk polish.
6. Phase F (holistic review + finishing): final pass.

Each phase keeps tests green throughout. Existing operator workflows aren't disrupted because settings carry forward; only new installs see the new defaults.

## Success criteria

- New `*-aify` install + register + dispatch produces a wrapper PTY by default (no synth for wrapper-backed runtimes).
- `agent.session_handle` is non-empty within 60s of a managed agent receiving its first dispatch — regardless of fresh-launch vs resume.
- Managed agent with no live worker shows status `available`, not `online`.
- During a 10-min subagent dispatch, status stays `working` end-to-end (no flapping).
- Operator can launch hermes-aify → register sc-hermes-test-1 → ping from sc-hermes-test-2 — message delivers and gets a real reply (not "Hermes isn't configured yet").
- `./redeploy.sh` reinstalls all detected wrappers correctly.
- codex-aify resume with a real session id succeeds (no false "not found" fallback).
- Console widget shows wrapper PTY for codex/hermes/pi managed; doesn't flip on input.
- `node --test mcp/stdio/tests/` and `python -m pytest service/tests/` both green.

## Open questions

None — operator approved the three architectural picks (`ready` as new status, drop hermes-session-resume, pi recon during implementation).

## References

- Plans 1+2+3 specs/plans in `docs/superpowers/`
- Task #117 (this plan's progenitor task) — issue catalog
- Memory `feedback-clean-architecture-always.md`, `feedback-500-line-rule.md`
- Operator-verified evidence from this session's live testing
