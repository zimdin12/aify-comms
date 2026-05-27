# AFK Holistic Stabilization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Review the main aify-comms happy paths end to end, fix high-confidence regressions found during review, and document deeper follow-up risks that need schema/API migration.

**Architecture:** Keep messages as the source of truth and dispatch runs as delivery attempts. Avoid broad rewrites in the existing dirty worktree; make isolated fixes with focused regression tests. Preserve managed/resident ownership boundaries: wrapper-backed managed delivery should be visible through the backing PTY, and resident delivery should not pretend a stale managed console is live.

**Tech Stack:** FastAPI/SQLite service, Node MCP stdio bridge, shell wrappers, Dashboard Next browser app.

---

## Review Findings

Subagent review plus local tracing found these concrete risks:

- Wrapper helpers: `codex-aify` and `hermes-aify` start background helper processes and then `exec` the foreground runtime, so `EXIT` traps cannot clean up helpers after the wrapper is replaced.
- Heartbeat posters: session-handle and turn-busy heartbeat POST/PATCH calls bypass `X-API-Key`, unlike the shared bridge HTTP client.
- MCP testability: `tests/codex-session.test.js` prints success but does not exit, preventing the standard MCP suite from completing.
- Wrapper-backed dispatch race: the main environment bridge can claim `channel` runs for wrapper-backed Codex/Hermes before the wrapper child is ready, bypassing the backing PTY.
- Dashboard Next send semantics: the chat composer defaults `queueIfBusy=true`, so a normal Send can silently become next-turn queue instead of live/steer.
- Dashboard Next status taxonomy: several backend states render as `unknown`.
- Dashboard Next resident console: cached/stopping managed terminal IDs can keep rendering xterm after switching to resident.
- Backend terminal-run ownership: terminal dispatch runs are not bound to a `terminal_id`, so stale-run cleanup and old-terminal stop cleanup can affect the wrong terminal-backed run.
- Session-mode switching: resident<->managed flips can grant capabilities without a live candidate/backing in some edge cases.
- Runtime capability drift: Pi/OpenCode resident compatibility paths still leak into dispatch in places despite current managed-only/unsupported resident behavior.

## Scope For This Pass

Implement now:

- [x] Heartbeat auth headers.
- [x] CodexSession RPC/readline cleanup so the MCP test suite exits.
- [x] Dashboard Next defaults and pure UI selectors/status mapping.
- [x] Claude channel import guard so pure-helper tests do not start the MCP bridge.
- [x] Docs/skills notes for the fixed behavior plus explicit backlog for deeper ownership migrations.
- [x] Focused JS verification plus health checks.

Document/defer unless a small, isolated fix appears during implementation:

- [ ] Add terminal ownership to `dispatch_runs` or a run-terminal link.
- [ ] Restrict wrapper-backed channel claims to wrapper-child bridges or live app-server/gateway ownership.
- [ ] Harden session-mode switch preconditions across backend and both dashboards.
- [ ] Remove/normalize legacy Pi/OpenCode resident dispatch leakage.
- [ ] Rewrite wrapper lifecycle away from `exec` so helper cleanup traps run.

## Task 1: Heartbeat Auth

**Files:**
- Modify: `mcp/stdio/session-handle-heartbeat.js`
- Modify: `mcp/stdio/turn-busy-heartbeat.js`
- Create or modify tests under `mcp/stdio/tests/`

- [x] Add failing tests proving `makeDefaultHandlePoster(baseUrl, apiKey)` and `makeDefaultTurnBusyPoster(baseUrl, apiKey)` include `X-API-Key` when `apiKey` is non-empty.
- [x] Implement optional `apiKey` parameters and only add the header when set.
- [x] Wire `server.js` to pass `API_KEY` into both poster factories.
- [x] Run the heartbeat tests.

## Task 2: CodexSession Test Exit

**Files:**
- Modify: `mcp/stdio/runtimes.js` or `mcp/stdio/codex-session.js`
- Test: `mcp/stdio/tests/codex-session.test.js`

- [x] Reproduce with `timeout 20s node tests/codex-session.test.js`.
- [x] Fix the root cause so child RPC/readline resources close after `CodexSession.stop()`.
- [x] Verify the same timeout command exits before timeout with status 0.
- [x] Fix the separate `claude-channel.js` import side effect that kept `npm test` from reaching the last test.
- [x] Run `npm test` in `mcp/stdio`.

## Task 3: Dashboard Next Defaults And Console Selection

**Files:**
- Modify: `service/new_dashboard/index.html`
- Modify: `service/new_dashboard/app.js`
- Modify: `service/new_dashboard/app.test.mjs`

- [x] Add failing tests for composer queue default, backend status mappings, and resident/stopping-terminal console choice.
- [x] Make normal Send default to live/steer by leaving `queueIfBusy` unchecked.
- [x] Expand `STATUS_KINDS` for backend states such as `idle`, `stale`, `stopped`, `lost`, `recovering`, and `unreachable`.
- [x] Prevent cached/stopping managed terminals from rendering xterm when selected session/agent is resident.
- [x] Run `node --test service/new_dashboard/app.test.mjs`.

## Task 4: Docs And Skills

**Files:**
- Reviewed: `README.md` (already carried the relevant normal-send/manual-switch semantics)
- Modify: `docs/HERMES_INTEGRATION.md`
- Modify: `.agents/skills/aify-comms/SKILL.md`
- Modify: `.agents/skills/aify-comms-debug/SKILL.md`
- Mirror to `.claude/skills/aify-comms*/` where those files contain the same relevant text.

- [x] Document that normal Dashboard Next Send is live/steer by default and Queue is explicit.
- [x] Document that resident console does not imply the old managed PTY remains current.
- [x] Add a debug/backlog note for wrapper-backed channel claim races and terminal-run ownership.
- [x] Keep wording concise and operational.

## Task 5: Verification And Commit

**Files:**
- Commit only files touched by this pass.

- [x] Run focused tests from Tasks 1-3.
- [x] Run `bash -n install.sh`.
- [x] Run `git diff --check`.
- [x] Run `docker compose up -d --build` if service/dashboard files changed.
- [x] Health-check `http://localhost:8800/health` and `http://localhost:8801/health`.
- [ ] Commit with a focused message.
- [ ] Push if credentials are available; otherwise report the exact push blocker.

## Self-Review

- No placeholder tasks remain; deferred items are explicit backlog with reasons.
- The plan does not require broad schema/API changes before smaller, high-confidence fixes.
- The plan honors current dirty worktree constraints by avoiding unrelated Pi/service edits.
