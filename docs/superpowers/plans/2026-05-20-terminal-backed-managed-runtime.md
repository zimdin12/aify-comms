# Terminal-Backed Managed Runtime Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make managed agents use a single terminal-backed runtime model where chat is API/MCP UI, Console is operator attach/view/control, and runtime wrappers own delivery/status.

**Architecture:** Introduce an authoritative backing runtime terminal/process per managed agent. Dashboard Console attaches to that backing process without becoming a separate delivery session. Backend status aggregates explicit wrapper status, active dispatch runs, and terminal liveness; it never treats random console bytes as work and never writes hidden text into an operator input buffer.

**Tech Stack:** FastAPI/SQLite service (`service/routers/api_v2.py`), static old dashboard (`service/dashboard.html`), stdio bridge/runtime wrappers (`mcp/stdio/server.js`, `mcp/stdio/runtimes.js`), Python `unittest`, Node stdio tests.

---

## File Structure

- `service/routers/api_v2.py`: settings defaults, dispatch routing, terminal/backing session state, status aggregation, Work Loop reminder timing.
- `service/dashboard.html`: settings UI and Console attach semantics for old dashboard.
- `service/tests/test_api_v2_regressions.py`: service regressions for reminder defaults, backing terminal status, delivery contracts, and Console attach behavior.
- `mcp/stdio/server.js`: terminal control processing and wrapper status event ingestion.
- `mcp/stdio/runtimes.js`: per-runtime launch/capability logic and explicit status/failure interpretation.
- `mcp/stdio/tests/*.test.js`: runtime/terminal/turn-busy/status regressions.
- `install.sh`: wrapper command generation when runtime wrapper semantics change.

---

### Task 1: Safe Settings Baseline

**Files:**
- Modify: `service/routers/api_v2.py`
- Modify: `service/dashboard.html`
- Modify: `service/tests/test_api_v2_regressions.py`

- [ ] **Step 1: Write failing default reminder test**

Add to `test_settings_include_dashboard_appearance_defaults`:

```python
self.assertEqual(settings.json()["reply_reminder_minutes"], 10)
self.assertEqual(settings.json()["reply_reminder_repeat_minutes"], 10)
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
python -m unittest service.tests.test_api_v2_regressions.ApiV2RegressionTests.test_settings_include_dashboard_appearance_defaults
```

Expected: FAIL with `6 != 10`.

- [ ] **Step 3: Implement default and UI text**

Change `DEFAULT_SETTINGS["reply_reminder_minutes"]` and `DEFAULT_SETTINGS["reply_reminder_repeat_minutes"]` to `10`, change internal fallback reads for both reminder values to use `DEFAULT_SETTINGS[...]`, and update the old dashboard hints to `Default: 10`.

- [ ] **Step 4: Run focused test**

Run:

```bash
python -m unittest service.tests.test_api_v2_regressions.ApiV2RegressionTests.test_settings_include_dashboard_appearance_defaults
```

Expected: OK.

---

### Task 2: Backing Terminal State Contract

**Files:**
- Modify: `service/routers/api_v2.py`
- Modify: `service/tests/test_api_v2_regressions.py`

- [ ] **Step 1: Write failing status tests**

Add tests asserting:

```python
# active Claude terminal run + missing terminal_id => status blocked
# active Claude terminal run + stopped terminal_status => status blocked
# attached backing terminal + no active run => status active
```

Use existing helpers `_create_running_session`, `_dispatch`, `_execute`, and `/api/v1/agents` assertions.

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m unittest service.tests.test_api_v2_regressions.ApiV2RegressionTests.test_managed_claude_active_run_without_terminal_backing_reports_blocked service.tests.test_api_v2_regressions.ApiV2RegressionTests.test_managed_claude_active_run_with_ended_terminal_backing_reports_blocked
```

Expected: FAIL before implementation because an active run without live backing reports `working`.

- [ ] **Step 3: Implement conservative status aggregation**

In `_compute_live_status_cache`, before `elif active_run`, classify terminal-mode Claude active runs without `terminal_id` or with terminal status outside `_TERMINAL_ACTIVE_STATUSES` as `blocked` with a note containing `no live terminal backing`.

- [ ] **Step 4: Run focused status tests**

Run the same command from Step 2. Expected: OK.

---

### Task 3: Console Is Attach/View/Control, Not Hidden Input

**Files:**
- Modify: `service/routers/api_v2.py`
- Modify: `service/dashboard.html`
- Modify: `service/tests/test_api_v2_regressions.py`

- [ ] **Step 1: Write failing Console-delivery tests**

Update Claude terminal-delivery tests to require one bracketed paste+submit input control for the message and no separate submit control. Add a test proving Claude development-channel auto-confirm is off by default and only adds the startup Enter when `console_auto_confirm_claude_dev_channels=true`.

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
python -m unittest service.tests.test_api_v2_regressions.ApiV2RegressionTests.test_managed_claude_dispatch_uses_claude_aify_terminal_turn service.tests.test_api_v2_regressions.ApiV2RegressionTests.test_managed_claude_auto_confirm_dev_channel_prompt_is_operator_toggle service.tests.test_api_v2_regressions.ApiV2RegressionTests.test_message_send_to_managed_claude_starts_claude_aify_and_inputs_dashboard_message
```

Expected: FAIL before implementation due extra raw input/Enter controls.

- [ ] **Step 3: Implement minimal safety behavior**

Set Claude/Hermes terminal delivery to use `_console_dispatch_input_body(..., bracketed_paste=True)` and remove the extra Claude submit control. Add setting `console_auto_confirm_claude_dev_channels` default `False`; only enqueue the startup Enter in `_ensure_managed_pty_for_dispatch` when that setting is enabled. Add the setting to old dashboard Runtime settings.

- [ ] **Step 4: Run focused tests**

Run the command from Step 2 plus the existing console-open Claude message test. Expected: OK.

---

### Task 4: Wrapper-Owned Status Events

**Files:**
- Modify: `mcp/stdio/server.js`
- Modify: `mcp/stdio/runtimes.js`
- Modify: `mcp/stdio/tests/turn-busy.test.js`
- Modify: `mcp/stdio/tests/terminal-runtime.test.js`

- [ ] **Step 1: Add explicit runtime status event tests**

Add stdio tests for wrapper events: `turn_start`, `turn_end`, `blocked`, `idle`, `fatal`. Assert server translates them into `agent_turn_state`/dispatch patch behavior without relying on terminal byte heuristics.

- [ ] **Step 2: Implement event ingestion**

Add a single event normalization path in `server.js` that accepts wrapper status events and patches service status/turn state. Keep existing terminal output heuristics as fallback only.

- [ ] **Step 3: Run stdio focused tests**

Run:

```bash
node mcp/stdio/tests/turn-busy.test.js
node mcp/stdio/tests/terminal-runtime.test.js
```

Expected: all assertions passed.

---

### Task 5: Native Runtime Migration Behind Feature Flags

**Files:**
- Modify: `service/routers/api_v2.py`
- Modify: `mcp/stdio/runtimes.js`
- Modify: `service/tests/test_api_v2_regressions.py`
- Modify: `mcp/stdio/tests/managed-native-capabilities.test.js`

- [ ] **Step 1: Add feature flag tests**

Add settings/feature tests proving Codex/Pi/OpenCode still use native managed dispatch when terminal-backed mode is off and use backing terminal contracts when `managed_terminal_backing_enabled=true`.

- [ ] **Step 2: Implement feature flag and routing**

Add `managed_terminal_backing_enabled=true` default after operator approval. In dispatch preflight/finalization, route Codex/Pi/OpenCode to terminal-backed delivery when the flag is enabled and a supported backing wrapper exists; preserve native managed dispatch as the fallback when the flag is disabled or no backing can be established.

- [ ] **Step 3: Run migration tests**

Run:

```bash
python -m unittest service.tests.test_api_v2_regressions.ApiV2RegressionTests.test_managed_dispatch_native_runtime_uses_terminal_backing_by_default service.tests.test_api_v2_regressions.ApiV2RegressionTests.test_managed_dispatch_native_runtime_can_fall_back_to_native_when_terminal_backing_disabled
npm test --prefix mcp/stdio
```

Expected: current native behavior remains green with flag off; new flag-on tests pass.

---

### Task 6: Final Verification and Rollout

**Files:**
- No new files.

- [ ] **Step 1: Run full service regressions**

```bash
python -m unittest service.tests.test_api_v2_regressions service.tests.test_new_dashboard_app
```

Expected: OK.

- [ ] **Step 2: Run full stdio suite**

```bash
npm test
```

from `mcp/stdio`. Expected: all assertions passed; known win32 `AttachConsole failed` noise may appear from node-pty.

- [ ] **Step 3: Rebuild and verify live health**

```bash
docker compose up -d --build
```

Then check:

```bash
curl http://127.0.0.1:8800/health
```

Expected: `{"status":"healthy"}`.

- [ ] **Step 4: Live smoke**

Verify one agent in each runtime class:
- Claude/Hermes/Codex/Pi/OpenCode with flag on: terminal-backed run shows `working`, then `active` or `blocked` correctly.
- Codex/Pi/OpenCode with flag off or unavailable backing: native delivery still works.
- Work Loop reminder repeat default is 10 minutes from `/api/v1/settings`.

---

## Self-Review

- Spec coverage: plan covers chat-vs-console separation, backing PTY/source-of-truth status, wrapper-owned status events, no direct hidden console input, hidden console status tracking, native migration, 10-minute reminders, tests, and rollout.
- Placeholder scan: no TBD/TODO placeholders remain.
- Type consistency: setting names are consistent: `reply_reminder_repeat_minutes`, `console_auto_confirm_claude_dev_channels`, and `managed_terminal_backing_enabled`.
