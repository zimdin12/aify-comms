# Test-suite sanity review — findings + plan (2026-06-12)

Operator ask: "look over our tests. are they reasonable. i am sure we have stupid tests
that should not exist and that we are missing some tests."

Inventory: **59 Python files / ~750 tests (25.2k lines)** + **121 JS files / ~540 tests
(15.6k lines)**. Suites are green (749 py; 536/540 js — the 4 = documented backlog).

## Findings

### F1 — `test_api_v2_regressions.py` is a 14,547-line monolith (CRITICAL, structural)
350 tests in 2 classes — 29× the repo's 500-line rule. This is where low-value tests
accumulate unseen, the file is too big for an agent to reason about whole, and every
merge conflicts here. **Action (own workstream, not this pass):** split by concern into
`test_regr_status_*.py`, `test_regr_dispatch_*.py`, `test_regr_sessions_*.py`,
`test_regr_bridges_*.py`, mechanical move-only (no behavior edits), one commit per chunk,
suite green between chunks.

### F2 — install.sh text-window tests are brittle by construction (KNOWN, accept + contain)
7 `test_install_*.py` files grep `install.sh` source text. They have failed twice on
unrelated edits (fixed +N-char windows). They are still the only way to test bash without
executing it. **Rule going forward:** sentinel-comment bounded sections only (already
applied to `test_install_hermes_leak`), never fixed character offsets/windows.

### F3 — JS wall-clock-margin tests (KNOWN class, remedy established)
15 JS test files wait on real `setTimeout` margins. Two already broke on the Windows
~15ms `setInterval` floor (fixed in `bc61d35` by sizing intervals ≥25ms with
proportional windows). **Rule:** any new timer-loop test uses intervalMs ≥ 25 and asserts
transitions, never tick counts at small intervals.

### F4 — coverage GAPS found this pass
- **Managed-claude coldstart-on-send** (`9d81ea8` fix): the channel-mode claude branch's
  fallback to `_coldstart_spawn_request_for_dispatch` had NO test → added in this pass
  (`test_dispatch_claude_coldstart.py`).
- **Env-bridge restart sweep end-to-end**: nothing asserts that after a bridge
  generation change, queued channel runs for managed agents recover (the 2026-06-12
  evening incident class). Candidate: integration test seeding a superseded
  wrapper-child + queued run → reconcile → spawn_request exists.
- **Release/adopt driver FSM**: covered as of this pass (3 tests in
  `test_agent_session_mode_switch.py`); the claude sidecar DORMANT-recheck behavior is
  bridge-side and untested (needs a claude-channel poll-loop harness — candidate).

### F5 — low-value tests to prune (small, safe)
- Duplicated assertions between `test_session_mode_fsm.py` and
  `test_agent_session_mode_switch.py` (the release-on-switch behavior is asserted in
  both; keep the FSM one as canonical, the switch file asserts the NEW adopt behavior).
  No deletion yet — they currently assert complementary sides; revisit after F1's split.
- No "assert constant == constant" or tautological tests found in sampling — the suite
  is in better shape than feared; the real problem is F1's structure, not stupidity.

## Order of work
1. (done this pass) F4 coldstart test.
2. F1 split — subagent-friendly mechanical workstream, ~4 commits.
3. F4 env-restart-recovery integration test.
4. F4 claude-channel poll-loop harness + dormant-release test.
