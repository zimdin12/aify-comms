# Plan 5 — Fix Plan 4's three root causes

**Date:** 2026-05-25
**Branch:** feature/dashboard-console-mode (continuation; Plan 4 shipped at 1c8d1c8)
**Goal:** Resolve the three confirmed root causes that block managed wrapper-backed dispatch for codex/hermes/pi and cause stale `online` status. Phase 1 of systematic debugging produced concrete evidence — see "Root causes" below.

---

## Background

Plan 4 (commits 290818c..1c8d1c8) flipped `managed_via_wrapper=["codex","hermes","pi"]` + `managed_pty_eager_spawn=True` as defaults, set `execution_mode='channel'` for wrapper-backed managed dispatches, added per-adapter `discoverSessionId`, and introduced a `ready` status. After deployment, live testing surfaced six symptoms across codex/hermes/pi. Phase-1 evidence-gathering reduced them to three independent root causes (one per logical concern).

The operator approved three fix shapes via AskUserQuestion:
- Pre-build hermes `web_dist` at install
- Symmetric managed channel-claim per runtime (analogue to `claude-channel.js`)
- Move `has_live_worker` check into the API read path

---

## Root cause 1 — Hermes-aify dashboard probe fails silently

**Evidence:** `C:\Users\dev\.local\state\aify-comms\hermes-aify-dashboard-{49931,59721}.log` (240 bytes each, dated 2026-05-25 22:31–22:33) all contain only:

```
✗ --skip-build was passed but no web dist found at: C:\Users\dev\AppData\Local\hermes\hermes-agent\hermes_cli\web_dist
  Pre-build first:  cd web && npm install && npm run build
  Or drop --skip-build to build automatically.
```

`hermes-aify` line 146/148 invokes `hermes dashboard --tui --port <P> --skip-build`. The dashboard subcommand dies immediately. `wait_for_http` times out after 30 s. Line 164 falls back to `exec hermes "${HERMES_ARGS[@]}"` — plain hermes without `AIFY_HERMES_GATEWAY_URL`, `AIFY_HERMES_GATEWAY_TOKEN`, or `HERMES_TUI_GATEWAY_URL` exported. The aify-comms MCP child loads into a hermes process with no gateway env, so the server stores `runtime_config={}` and computes `wakeMode='hermes-missing-handle'` (registers ok, but server can't push wake to the gateway).

This also explains operator's hermes agent-2 confusion: delivery to sc-hermes-test-1 *did* succeed (`claim_bridge_id=744f24c0-...`, message reached hermes), but the hermes process inside replied "no API keys" — a SEPARATE problem from the missing-gateway-url. Agent-2's "delivery works" claim was correct.

**Fix:** Add a hermes-install branch to `install.sh` that runs `cd <hermes-install-root>/web && npm install && npm run build` once during install (or on `install.sh --client hermes`). Wrapper keeps `--skip-build` for fast launch on every subsequent run.

Additionally, the wrapper should emit a more visible warning when it falls back to plain hermes — operator should see "AIFY_HERMES_GATEWAY_URL not exported because dashboard probe failed; check $LOG_FILE" instead of a silent fallthrough.

---

## Root cause 2 — Channel-claim asymmetry between claude and codex/hermes/pi

**Evidence:**

- `service/routers/api_v2.py:1047-1048` returns `execution_mode='channel'` for any wrapper-backed runtime (including codex/hermes/pi).
- `service/routers/api_v2.py:260` defines `_CHANNEL_MANAGED_RUNTIMES = {"claude-code"}` — the whitelist of runtimes whose bridges may claim `execution_mode='channel'`.
- `service/routers/api_v2.py:11163` gates each claim: `channel_claim = agent_runtime in _CHANNEL_MANAGED_RUNTIMES and "channel" in supported_modes`. Codex/hermes/pi bridges are rejected even if they ask for "channel".
- `mcp/stdio/dispatch-execution.js:13-41` returns `[]` for `sessionMode='managed'` + wrapper-backed (line 22-31 explicitly excludes wrapper-backed from the managed branch). The bridge never even requests "channel" in its claim poll.
- Live evidence: `dispatch_runs.run_1779737289805_4ad68eb5` for graph-senior-dev (codex managed): `status=queued, execution_mode=channel, claim_bridge_id=''`. The bridge `4fc0e4a2-...` for graph-senior-dev is alive (last_seen 19:47:51 Z) but never claims this run.

Plan 4 set the server route but not the bridge claim. Three symptoms collapse here:
- Bug 2 (graph-senior-dev no answer)
- Bug 3 (pi managed never answers)
- Bug 4 (hermes managed never answers)

**Fix shape (per operator):** Symmetric channel-claim per runtime, analogous to `claude-channel.js`:

1. **Server side** (`service/routers/api_v2.py`):
   - Extend `_CHANNEL_MANAGED_RUNTIMES` to include `codex`, `hermes`, `pi`.
2. **Bridge side** (`mcp/stdio/dispatch-execution.js`):
   - For `sessionMode='managed'` + wrapper-backed + runtime in `{codex,hermes,pi}` → push `'channel'`.
3. **Delivery side** (`mcp/stdio/controllers/*-controller.js`):
   - On `executionMode='channel'` + `sessionMode='managed'` + wrapper-backed, route to the same controller used today for `executionMode='channel'` from the resident-channel path (Plan 3 — `CodexLegacyController` for codex, the gateway-WS path for hermes, the nested-RPC path for pi). The bridge is already inside the wrapper PTY, so the runtime-native inject path is reachable.

Architectural property: the bridge running inside a wrapper-backed managed PTY is structurally identical to a resident-mode bridge (same process, same runtime-native inject path). The only thing that varies is the `sessionMode` flag in the registration. Symmetric claim respects that.

---

## Root cause 3 — `agent_live_state` cache lies about `online`

**Evidence:**

- `service/routers/api_v2.py:2228-2462` (`_compute_live_status_cache`) computes `has_live_worker` correctly: lines 2319-2345 check `terminal_sessions` for a row whose `command` matches `*-aify` or `RPC_SET`.
- BUT the resulting status is written to `agent_live_state` and the cache's `refresh_after` (line 2454-2462) is keyed off heartbeat freshness via `_status_refresh_after(agent_last_seen, env_last_seen, ...)` — NOT worker presence.
- When the wrapper PTY exits but a parallel heartbeat keeps the agent alive (e.g., the operator's claude-aify session's bridge polling on its own), `refresh_after` stays in the future and the cache is never re-validated.
- `_refresh_expired_agent_live_states` at `api_v2.py:2760` recomputes only when `refresh_after <= now`. With fresh heartbeats, `refresh_after` is always in the future. Stale `status='online'` persists indefinitely.
- Live evidence: graph-senior-dev `agent_live_state.terminal_id=''` but `status='online'` (`updated_at=19:29:10 Z`, `refresh_after=19:30:28 Z` was 18+ min ago — past but still no re-validation triggered on the actual read).

Implementer of Plan 4 Task 10 claimed "the live-state cache already implements Plan 4 status taxonomy correctly." That claim is false — they only patched the db-less fallback path.

**Fix shape (per operator):** Move the `has_live_worker` validation into the agent read path (`GET /api/v1/agents` and `GET /api/v1/agents/{id}`). Cache stays for performance but doesn't lie:
- Before returning `status='online'` for a managed-wrapper-backed agent, validate that a non-`stopped`/`failed` `terminal_sessions` row exists for the agent (or a live RPC controller binding).
- If validation fails, return `available` (worker absent but agent reachable) regardless of cached value.
- Cache writeback updates `agent_live_state.status` to keep dashboard polling correct.

This is a single check at the API boundary — cheap and self-correcting on every read.

---

## Out of scope (defer)

- **Bug 5 (claude auto-accept dev channels):** Operator was uncertain; no concrete failure. Don't speculate.
- **Plan 3 follow-up #123 (runtimes.js per-concern extraction):** Existing pending task, separate concern.
- **Codex/pi/hermes managed live e2e tickets #87, #97, #114:** Will be naturally exercised by the fix verification.

---

## Success criteria

- `comms_send` from one agent to `graph-senior-dev` (codex managed) gets a real answer back in chat.
- Same for any pi-managed and any hermes-managed agent (assuming hermes has API keys — config issue is the operator's, not ours).
- Operator launches a fresh `hermes-aify` after the install fix: `AIFY_HERMES_GATEWAY_URL` is set in the hermes shell env; `comms_agent_info <hermes-resident>` reports `wakeMode='hermes-live'`.
- Dashboard agent list does NOT show `online` for any managed agent that has no live `terminal_sessions` row.
- All existing tests pass; new TDD tests cover each of the three fixes.

---

## Implementation outline

3 sections (A/B/C), ~12 tasks total. Section B is the heaviest (per-runtime claim + delivery wiring).

**Section A — hermes web_dist pre-build (install-side fix):**
- A1. Add a `prebuild_hermes_web_dist()` step to `install.sh`'s hermes branch.
- A2. Modify wrapper to log a clearer warning when it falls back to plain hermes (operator visibility).
- A3. Document in `install.hermes.md`.

**Section B — Symmetric channel-claim for codex/hermes/pi:**
- B1. (TDD) failing tests that assert codex/hermes/pi wrapper-backed bridges include `'channel'` in `supportedExecutionModes` output.
- B2. Implement: extend `dispatch-execution.js:supportedExecutionModes` to push `'channel'` for managed + wrapper-backed + runtime in codex/hermes/pi.
- B3. (TDD) failing test against service: `POST /dispatch/claim` from a codex/hermes/pi bridge with `executionModes=['channel']` returns the queued channel-mode run.
- B4. Implement: extend `_CHANNEL_MANAGED_RUNTIMES` in `api_v2.py` to include codex/hermes/pi.
- B5. (TDD) failing controller test: when channel-claim returns for codex managed wrapper-backed, the controller route lands on `CodexLegacyController` (or whichever existing controller does the inject — implementer's recon decision).
- B6. Implement: ensure `controllers/codex-controller.js`, `controllers/hermes-controller.js`, `controllers/pi-controller.js` route `executionMode='channel' + sessionMode='managed' + wrapper-backed` to the same inject path they already use for resident-channel.
- B7. Smoke: rebuild container, hand-send a message to graph-senior-dev, verify response.

**Section C — `has_live_worker` gate in read path:**
- C1. (TDD) failing test: `GET /api/v1/agents` for a managed agent with no live `terminal_sessions` row returns `status != 'online'` even when `agent_live_state.status='online'` is cached.
- C2. Implement: in the agent serializer (the function that builds the JSON response from `agent_live_state` row), validate live worker for managed wrapper-backed agents; downgrade to `available` if absent.
- C3. Add a cache writeback so the next read is also corrected.

**Section D — Holistic review + docs:**
- D1. Run full python + js test suites.
- D2. Dispatch code-reviewer subagent across the full Plan 5 diff.
- D3. Update `DECISIONS.md` with the three root cause writeups + fix rationale (so future-me doesn't re-introduce these gaps).
- D4. Update `.claude/skills/aify-comms-debug/SKILL.md` with detection recipes for each (so a debug-skill reader recognises the symptoms).
- D5. `superpowers:finishing-a-development-branch` — present 4 options to operator.

---

## Non-goals

- Do NOT rebuild Plan 4's status taxonomy from scratch. Surgical fix to the cache lie only.
- Do NOT change `execution_mode='channel'` routing for wrapper-backed; that part of Plan 4 was correct.
- Do NOT touch claude-code paths. Claude works.
- Do NOT touch opencode (operator memory: never run opencode tests; opencode stays unimplemented for channel-claim).
