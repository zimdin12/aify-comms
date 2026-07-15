# Aify Comms: New Dashboard, Delivery, Registration, and Lifecycle Remediation Plan

> **For the implementation owner:** execute each task with a red regression first, then the smallest root-cause patch, then focused and full verification. Do not touch the retired monolithic dashboard except to delete it and remove compatibility wiring.

**Goal:** Repair the eight reported communication/lifecycle defects, preserve aify-comms routing and ownership semantics, make the new dashboard the only dashboard, and prove fixes through the real service/bridge paths.

**Architecture decision:** Keep the API/message/run/session models unchanged. Fix behavior at the owning layer: new-dashboard SPA for operator interactions, service for durable message/lifecycle semantics, bridge/runtime controllers for process truth, and Hermes integration for resident linkage. Preserve Console as a real PTY. Expose Hermes’ browser UI separately so it cannot hijack Console.

**Primary stack:** FastAPI + SQLite service, vanilla ES-module dashboard on port 8801, Node stdio/environment bridges, runtime adapters/controllers, Python/Node tests, Docker Compose.

---

## Evidence gathered before implementation

- `service/new_dashboard/` is the active dashboard. `service/dashboard.html` is the obsolete 8800 monolith and still has live route, CLI, test, documentation, and “Old dashboard” references.
- The new dashboard’s mode-switch POST already calls `refreshSoon()`, so the reported stale UI must be reproduced against the deployed browser before changing it; likely causes include refresh coalescing/cache or DOM state, not a missing call.
- The new chat composer sends `requireReply: false` when its checkbox is clear, but browser form-state restoration can preserve a prior checked state. Wire behavior must be captured before deciding whether only explicit reset/labeling is needed.
- Reminder creation must preserve `from_agent=contract["from_agent"]`, per Steven's explicit decision, so the recipient answers the original requester rather than a `dashboard` intermediary. The real gap is proving that persisted reminder authorship and linked completion preserve the original request/reply chain.
- Bridge claim logging already has first/100th/recovery throttling in source. The reported ~2-second spam must be tested against the actual running bridge/version before altering logging.
- Claude channel source already has a dead-parent beat gate and two-check self-exit guard. The live ghost requires a realistic process-tree test; pure predicate coverage alone is insufficient.
- Agent-scoped managed-Hermes teardown exists for terminal controls, but `/agents/{id}/stop-worker` only mutates service rows and assumes later reconcile will remove bridge resources. That leaves a no-terminal/no-control path where a gateway triad can survive.
- Manual `comms_register` from this active Hermes session reproducibly returns `ClosedResourceError`; a direct agent readback remains `stopped`/`wakeMode=disabled` despite a current resident session handle. Record existence is not linkage.
- Commit `742f049` intentionally prevents a Hermes web iframe from replacing Console. The requested browser UI must therefore be a separate explicit surface, not a revert that regresses Console.

---

## Task 1: Freeze real wire and process evidence

**Files:**
- Add/extend tests under `service/tests/`, `service/new_dashboard/*.test.mjs`, and `mcp/stdio/tests/`
- No production changes in this task

1. Capture API responses for mode switch, message send, reminder, stop-worker, and registration using the real request shapes.
2. Reproduce the new-dashboard mode-switch and composer behavior in a browser against port 8801; record network requests and resulting DOM state.
3. Start representative managed/resident agents and capture the real process tree before/after wrapper exit and stop-worker.
4. Record bridge version/doctor output and distinguish a stale deployment from a source defect.
5. Add red tests only for behaviors that still fail on current source.

**Gate:** each production patch below must cite a red test or a live failure trace. Do not change code for a stale report that current source and live deployment both disprove.

## Task 2: Make the new dashboard the only dashboard

**Files:**
- Delete: `service/dashboard.html`
- Delete: `service/tests/dashboard-console-copy.test.mjs`
- Delete: `service/tests/dashboard-run-note.test.mjs`
- Delete: `service/tests/test_dashboard_usage.py`
- Modify: `service/routers/api_v2.py`
- Modify: `service/main.py`
- Modify: `service/new_dashboard/app.js`
- Modify: `service/new_dashboard/index.html`
- Modify: `service/tests/test_new_dashboard_app.py`
- Modify: `mcp/stdio/server.js`
- Modify: `mcp/sse_server.py`
- Modify: current operator docs/skills (`README.md`, `.env.example`, `DECISIONS.md`, `docs/AGENT_GUIDE.md`, dashboard operation references)

1. Replace `/api/v1/dashboard`, `/api/v1/dashboard/dispatches`, and API-root legacy serving with a compatibility redirect to the new dashboard URL; retain entry-point compatibility rather than returning a broken 404.
2. Point `comms_dashboard`, SSE status output, and install/readme guidance at the new dashboard.
3. Remove “Old dashboard” and `openClassic()` controls from the new SPA.
4. Delete the monolith and its monolith-only smoke tests.
5. Update current authoritative docs/skills. Historical plans may retain historical references but must not instruct operators to use the retired surface.
6. Add route/CLI tests proving old URLs redirect and the new dashboard remains healthy.

**Gate:** no tracked production/test/current-doc reference may require `service/dashboard.html`; `/api/v1/dashboard` must reach the new dashboard.

## Task 3: Add Hermes browser UI without hijacking Console

**Files:**
- Modify: `service/new_dashboard/index.html`
- Modify: `service/new_dashboard/app.js`
- Modify: `service/new_dashboard/styles.css`
- Modify: `service/new_dashboard/console-chooser.js` only if a pure chooser is needed for the new tab
- Extend: `service/new_dashboard/console-chooser.test.mjs`, `service/new_dashboard/app.test.mjs`

1. Preserve PTY/xterm as the only Console-tab renderer.
2. When an agent exposes a loopback Hermes gateway, expose a separate explicit **Hermes UI** session tab/action.
3. Convert only loopback `ws(s)` gateway URLs to `http(s)` and preserve the scoped token; never embed public/non-loopback gateways.
4. Keep the existing “Open in new tab” fallback.
5. Ensure polling/re-render guards do not destroy the iframe or active xterm.
6. Test tab availability, loopback/security gating, and Console remaining xterm/none.

**Gate:** Console never renders the Hermes page; selecting Hermes UI renders the same gateway web UI Hermes serves.

## Task 4: Make mode switching visibly converge

**Files:**
- Modify if reproduction confirms: `service/new_dashboard/app.js`
- Extend: `service/new_dashboard/app.test.mjs` or a focused DOM test

1. Add a red test around the real click-handler/refresh path, not a string assertion.
2. After a successful switch, invalidate any in-flight/coalesced refresh and await a forced agents/sessions refresh, or apply the returned canonical agent then refresh.
3. Disable the switch control while pending and surface failure without changing local state.
4. Verify both resident→managed and managed→resident.

**Gate:** the chip/status/action set converges without manual page refresh after the POST succeeds.

## Task 5: Preserve original-requester reminder authorship and linkage

**Files:**
- Modify: `service/models.py` if the request model lacks an actor field
- Modify: `service/routers/api_v2.py`
- Modify: `service/new_dashboard/app.js`
- Extend: reminder/contract tests in `service/tests/`

1. Resolve the original request from the contract/message record; do not invent a new `dashboard` requester.
2. Insert and emit the reminder with the original requester's `from_agent`, keep `in_reply_to` anchored to the original message, and preserve reminder counts plus delivery/dispatch semantics.
3. Ensure a recipient reply/completion routes back to that original requester and records the resulting message ID.
4. Test the persisted reminder, emitted API payload, queued/delivered completion, and linked response.

**Gate:** the reminder is authored by the original requester and the recipient's linked reply reaches that requester; no `dashboard` intermediary is introduced.

## Task 6: Make new-dashboard `requireReply` opt-in and unambiguous

**Files:**
- Modify if reproduction confirms: `service/new_dashboard/index.html`, `service/new_dashboard/chat.js`
- Extend: `service/new_dashboard/chat.test.mjs`

1. Capture the outgoing JSON for a fresh plain composer send and for an explicitly checked send.
2. Prevent browser-restored checkbox state from silently becoming the default; initialize/reset it explicitly for a fresh conversation/session.
3. Keep `info + requireReply=false` as the default; do not infer a request merely from ordinary prose.
4. Keep explicit request/review/error types and explicit `requireReply=true` working.

**Gate:** fresh ordinary sends create no reply contract; opt-in sends do.

## Task 7: Verify and harden transient bridge logging

**Files:**
- Modify only if current integration test fails: `mcp/stdio/server.js`
- Extend: `mcp/stdio/tests/` with timed/repeated claim tests

1. Drive repeated spawn, terminal-control, and environment-control transient failures through the actual loops.
2. Assert one initial log, suppression between milestones, periodic milestone log, and one recovery log.
3. Use stable error keys/counters per loop; avoid logging every long-poll timeout/network flap.
4. Preserve immediate logs for permanent/schema/operator-action errors.

**Gate:** a multi-minute transient outage does not emit one error per poll, and recovery is visible exactly once.

## Task 8: Close resident ghost and managed-Hermes leak paths

**Files:**
- Modify as proven: `mcp/stdio/claude-channel.js`, bridge launcher/controller ownership metadata
- Modify: `service/routers/api_v2.py`
- Modify: `mcp/stdio/server.js`
- Extend: `mcp/stdio/tests/claude-channel-parent-guard.test.js`, managed teardown tests, service stop-worker tests

1. Replace predicate-only ghost coverage with a real child-process test that kills the controlling parent, observes immediate beat suppression, and observes sidecar exit after two checks.
2. Verify the captured parent PID is the actual visible runtime owner on Windows/WSL/Linux; if not, pass the owner PID explicitly from the launcher instead of trusting `process.ppid`.
3. Make stop-worker emit an agent-scoped bridge teardown control even when no terminal row exists.
4. Reuse the existing runtime/capability-driven managed teardown; do not add runtime branching in service state logic beyond an explicit capability/action payload.
5. Verify Stop, Remove, bridge exit, and crash/survivor-sweep paths with process/port/marker evidence and prove another agent/resident process is untouched.

**Gate:** no live sidecar/gateway/daemon remains for the stopped owner; unrelated agents survive.

## Task 9: Make manual Hermes registration establish a live resident binding

**Files:**
- Modify after transport trace: `mcp/stdio/server.js`
- Modify as needed: Hermes wrapper/plugin launcher (`install.sh` templates and/or `integrations/hermes-aify-plugin`)
- Extend: `mcp/stdio/tests/` registration/runtime-adapter tests
- Extend: service registration/readback tests

1. Reproduce `ClosedResourceError` with the Hermes MCP transport and record which process/resource closes.
2. Ensure a resumed/plain Hermes TUI has a fresh aify MCP transport and a discoverable native session handle before `comms_register` returns.
3. Make Hermes late registration symmetric with Claude’s late identity handling: persist the real native session ID, bind the current live bridge/session, start the correct Hermes delivery/turn-detection mechanism, and clear obsolete resident-lost state through normal registration/liveness evidence.
4. Return an error if live linkage cannot be established; never report success for a database-only identity.
5. Read back `/agents/{id}` and prove resident mode, matching handle, live bridge, enabled wake mode, and successful direct message delivery into that exact session.

**Gate:** `comms_register` succeeds from a plain/resumed Hermes TUI and a same-session message is delivered; record-only/offline registration fails honestly.

## Task 10: Review and regression gates

1. Run focused Node and Python tests per task.
2. Run `node --test mcp/stdio/tests/*.test.js` (or repository Make target), full `pytest service/tests`, `make test`, and `make build`.
3. Rebuild Compose and prove `:8800/health`, `:8801/health`, compatibility dashboard redirect, browser behavior, registration readback, message delivery, and process cleanup.
4. Run `aify-doctor --json` and verify running bridge/service source matches the patched tree.
5. Re-review the final diff for runtime symmetry, capability-based branching, token redaction, no cross-agent process killing, and no legacy dashboard resurrection.

**Evidence labels in final report:**
- **PROVEN:** exercised against the rebuilt real service/bridge/browser/process tree.
- **PASSES IN TESTS:** covered by unit/integration tests but not the live path.
- **ASSUMED/BLOCKED:** anything not observable; no strengthened claims.

## Console design recommendation

Do not embed the Hermes browser UI in Console and do not reconstruct a differential TUI from a truncated synthetic scrollback. Keep Console as the actual PTY surface. The current persistent server-side terminal screen plus attach/refresh repaint is the smallest correct short-term design because it consumes the same bytes and terminal dimensions as the visible runtime.

The next durable step is host-authored screen snapshots: the PTY-owning bridge should publish a monotonically sequenced full screen frame (and optionally bounded deltas between frames). Dashboard Next should mount the latest full frame first, then accept only contiguous deltas. A sequence gap must request a fresh snapshot rather than replaying log history. Hermes `tui_gateway` remains useful for session/prompt control, but it is not a substitute for the PTY screen and should stay a separate explicit surface.
