# Managed-Hermes Lifecycle Ownership + Restart Teardown — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make managed runtimes (hermes first, codex/pi by extension) bridge-owned with a single lifecycle owner, so a restart of `aify-comms` is a guaranteed clean slate, `online` means deliverable, queued runs can never silently pile up, and the visible console always reflects (and accepts input for) the process actually doing the work.

**Architecture:** The managed-hermes triad (gateway host, delivery loop, console PTY) gets a single supervisor — the **delivery loop** — that owns the gateway host by port, watches the console, registers/heartbeats before bringing the gateway up, and tears everything down + self-exits on any terminal condition (agent-deleted/410, dead gateway, gone console, release). The environment bridge gains a **shutdown teardown hook** (kill all managed children it owns) and a **boot-time survivor sweep** (reap crash/SIGKILL leftovers), both scoped to the bridge's `cwdRoots` and never touching resident sessions. The server stops inferring health from presence: managed hermes joins the channel-sidecar-delivery runtimes so `online` requires a live claimer, a new reaper fails never-claimed queued runs, sends to a deaf target fail-fast, and second-based staleness windows are replaced by event-driven turn/liveness signals with one long backstop. Cruft (markers, ghost rows, orphaned dispatch_runs) gets GC'd.

**Tech Stack:** Node ESM host bridges (`mcp/stdio/`), Python FastAPI + aiosqlite service (`service/`), `node --test`, `pytest`. Spec: `docs/superpowers/specs/2026-06-02-managed-hermes-lifecycle-and-restart-teardown-design.md`.

**Operator decisions baked in:** (1) deaf-target sends FAIL FAST + surface to sender (reaper backstops in-flight); (2) restart teardown blast radius = all managed sessions THIS env bridge owns (never resident/other-env); (3) WS5 event-driven ships in the SAME batch.

**Standing constraints:** never run opencode tests on this host; commit before any container rebuild (build COPYs the working tree); `mcp/stdio/` changes need a wrapper/bridge restart, not a rebuild; visible-TUI-in-dashboard is a HARD requirement (no headless, no popup windows, `windowsHide:true`); keep skill mirrors (`.claude/skills` + `.agents/skills`) in sync; forward-slash cwd for codex; commits end with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Work on branch `feature/managed-hermes-lifecycle` off `main`.

---

## File structure

| File | Responsibility | WS |
|------|----------------|----|
| `mcp/stdio/hermes-managed-host.js` | Delivery loop = triad supervisor: pre-gateway registration, terminal-condition self-exit, gateway-host port-kill teardown, ready-marker | 1 |
| `mcp/stdio/hermes-loop-ready.js` (new) | Write/read/clear the loop ready-marker (`aify-hermes-loop-ready-<agent>`) the wrapper health-gates on | 1 |
| `mcp/stdio/hermes-endpoint.js` | Marker cleanup (`clearGatewayMarkers(agentId)` for port+key) | 1,4 |
| `mcp/stdio/hermes-daemon.js` | `stopDaemon` also clears port/key markers | 4 |
| `install.sh` | Wrapper: health-gate the loop before TUI exec; kill-prior PID-excludes the new loop | 1 |
| `mcp/stdio/reap-managed-survivors.js` (new) | Enumerate + kill managed children (loops, gateway hosts, daemons, PTYs) scoped to an env's cwdRoots / a set of owned agents; never resident | 2 |
| `mcp/stdio/server.js` | Wire teardown into `shutdownWithStatus` + supersede path; boot-time survivor sweep for the env bridge; host-reported dead-PTY marking | 2,4 |
| `service/routers/api_v2.py` | hermes in `_CHANNEL_SIDECAR_DELIVERY_RUNTIMES`; gate `has_live_worker` console branch; queued-run backstop reaper; fail-fast deaf-target send; event-driven turn-state; dispatch_runs pruner | 3,4,5 |
| `service/main.py` | Wire new reapers into the reconcile loop | 3,4 |
| `service/db.py` | (if needed) index for queued-run age scan | 3 |
| `.claude/skills/**`, `.agents/skills/**`, `KNOWN_ISSUES.md`, `DECISIONS.md`, `README.md` | Docs/skills | 6 |

Each task is TDD: write the failing test, run it red, implement minimally, run green, commit.

---

## WS1 — Single lifecycle owner for the managed-hermes triad

### Task 1.1: Loop registers + heartbeats BEFORE the gateway await

**Files:** Modify `mcp/stdio/hermes-managed-host.js` (`runDeliveryLoop` ~:1040-1120); Test `mcp/stdio/tests/hermes-managed-host.test.js`

- [ ] **Step 1: Failing test** — assert that when `ensureGatewayHost` rejects, the loop has ALREADY called `startLivenessHeartbeat` (a `bridge_instances` registration) at least once, and does NOT call `process.exit` synchronously; instead it enters the retry path.

```js
test("loop registers liveness before gateway bring-up, retries on gateway failure", async () => {
  const beats = [];
  const fakeBeat = () => beats.push(Date.now());
  let gwCalls = 0;
  const ensureGatewayHost = async () => { gwCalls++; if (gwCalls < 2) throw new Error("index token timeout"); return { child: null, reused: true, wsUrl: "ws://127.0.0.1:9313" }; };
  const loop = makeDeliveryLoop({ ensureGatewayHost, startLivenessHeartbeat: () => { fakeBeat(); return { stop(){} }; }, claimOnce: async () => ({ processed: 0, release: false }), maxIterations: 2 });
  await loop.runUntilIdle();
  assert.ok(beats.length >= 1, "heartbeat started before/independent of gateway");
  assert.ok(gwCalls >= 2, "gateway bring-up retried, not fatal");
});
```

- [ ] **Step 2: Run red** — `node --test mcp/stdio/tests/hermes-managed-host.test.js` → FAIL (today the pre-loop `await ensureGatewayHost` throw → `process.exit(1)`).
- [ ] **Step 3: Implement** — reorder `runDeliveryLoop`: call `startLivenessHeartbeat()` and register the channel-sidecar bridge BEFORE gateway bring-up; move `ensureGatewayHost` into the retry loop body (bounded backoff) so a transient failure is non-fatal and never calls `process.exit` outside the terminal-condition handler (Task 1.3). Extract a testable `makeDeliveryLoop` seam if needed.
- [ ] **Step 4: Run green.**
- [ ] **Step 5: Commit** — `git commit -m "fix(hermes): loop registers liveness before gateway bring-up; gateway failure non-fatal\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"`

### Task 1.2: Gateway-host teardown works for a reused (child===null) host

**Files:** Modify `mcp/stdio/hermes-managed-host.js` (`teardownGatewayHost` ~:995, `ensureGatewayHost` ~:232-282); Test same file's tests.

- [ ] **Step 1: Failing test** — when `gatewayChild` is null (reused host) but a port is known, `teardownGatewayHost` calls `killByPort(port)` instead of no-opping.

```js
test("teardownGatewayHost kills by port when child is null (reused host)", async () => {
  const killed = [];
  const td = makeTeardown({ gatewayChild: null, gatewayPort: 9313, killByPort: (p) => killed.push(p) });
  await td();
  assert.deepEqual(killed, [9313]);
});
```

- [ ] **Step 2: Run red.**
- [ ] **Step 3: Implement** — persist the resolved gateway port on the loop state in `ensureGatewayHost` (even on `reused:true`); in `teardownGatewayHost`, if `child` is falsy and a port is known, call `defaultKillByPort(port)` (import from `hermes-daemon.js`).
- [ ] **Step 4: Run green.** **Step 5: Commit.**

### Task 1.3: Loop self-exits + tears down on terminal conditions (410 / dead gateway / release)

**Files:** `mcp/stdio/hermes-managed-host.js` (`runPollCycle` ~:955-984, `runDeliveryLoop` ~:1155-1200); Test same.

- [ ] **Step 1: Failing tests** — (a) a `/dispatch/claim` HTTP 410 makes the loop break, run teardown, and resolve a terminal result `{ exit: "agent-removed" }` (no swallow); (b) `release===true` still tears down the gateway host (not just exits).

```js
test("410 from claim is terminal: teardown + exit, not swallowed", async () => {
  let toreDown = false;
  const loop = makeDeliveryLoop({ claimOnce: async () => { const e = new Error("gone"); e.status = 410; throw e; }, teardown: () => { toreDown = true; } });
  const r = await loop.runUntilIdle();
  assert.equal(r.exit, "agent-removed");
  assert.ok(toreDown);
});
```

- [ ] **Step 2: Run red** (today 410 is caught + logged in `runPollCycle`, loop continues).
- [ ] **Step 3: Implement** — classify `err.status === 410` (and 404 after a small consecutive-count grace, reusing the existing 404 self-heal counters) as terminal in `runPollCycle`; propagate a terminal signal so `runDeliveryLoop` breaks, awaits `teardownGatewayHost`, clears markers (Task 4.1), and `process.exit(0)`. Keep transient WS/connect errors non-terminal (preserve current retry).
- [ ] **Step 4: Run green.** **Step 5: Commit.**

### Task 1.4: Loop writes a ready-marker after it is a live claimer

**Files:** Create `mcp/stdio/hermes-loop-ready.js`; modify `hermes-managed-host.js`; Test `mcp/stdio/tests/hermes-loop-ready.test.js`.

- [ ] **Step 1: Failing test** — `writeLoopReady(agentId, dir)` creates `aify-hermes-loop-ready-<agent>`; `loopReadyFresh(agentId, dir, maxAgeMs)` is true only within the window; `clearLoopReady` removes it.
- [ ] **Step 2: Run red.**
- [ ] **Step 3: Implement** `hermes-loop-ready.js` (mirror `hermes-daemon.js` pid-file helpers, mtime-based freshness). In `runDeliveryLoop`, call `writeLoopReady` after: gateway ok + heartbeat started + first successful `/dispatch/claim` round-trip (even if 0 runs). Refresh on each successful claim; `clearLoopReady` in teardown.
- [ ] **Step 4: Run green.** **Step 5: Commit.**

### Task 1.5: Wrapper health-gates the loop before exec'ing the TUI; kill-prior PID-excludes the new loop

**Files:** Modify `install.sh` (bash managed branch ~:1407-1455, PowerShell ~:1747-1797, kill-prior `aify_hermes_kill_prior` ~:1373-1378 / `Invoke-AifyHermesKillPrior` ~:1714-1718). Test: extend an existing `service/tests/test_install_hermes*.py` or add `service/tests/test_install_hermes_loop_gate.py` asserting the generated wrapper text contains the gate + exclusion (string/structure assertions, NOT a live launch).

- [ ] **Step 1: Failing test** — assert the generated `hermes-aify` wrapper (a) waits (bounded, e.g. 30s) for `aify-hermes-loop-ready-<agent>` after spawning the loop and before `exec hermes --tui` / `Invoke-HermesRuntime`, and emits a loud failure if it never appears; (b) kill-prior excludes the PID of the loop the wrapper just spawned.
- [ ] **Step 2: Run red.**
- [ ] **Step 3: Implement** — in both wrapper branches: capture the spawned loop PID; poll for the ready-marker with a timeout; on timeout print an explicit `aify: hermes delivery loop failed to become a live claimer` and exit non-zero (do NOT exec a TUI that can't receive work). Make kill-prior skip the just-spawned loop PID.
- [ ] **Step 4: Run green** (`pytest` the install test). **Step 5: Commit.**

---

## WS2 — Bridge restart teardown + boot-time survivor sweep (HARD requirement)

### Task 2.1: Reusable env-scoped managed-survivor reaper

**Files:** Create `mcp/stdio/reap-managed-survivors.js`; Test `mcp/stdio/tests/reap-managed-survivors.test.js`.

- [ ] **Step 1: Failing test** — `enumerateManagedSurvivors({ ownedAgentIds, cwdRoots, listProcesses, readMarkers })` returns gateway hosts (by `aify-hermes-port-*`), delivery loops (cmdline `hermes-managed-host.js run <agent>`), daemons (`aify-hermes-daemon-pid-*`), and console PTYs (`terminal_sessions.process_id`) ONLY for owned/in-root agents; and `reapManagedSurvivors(...)` calls the right kill primitive per kind and NEVER includes a process whose cmdline shows a resident `--aify-agent` operator session not in `ownedAgentIds`.

```js
test("enumerate excludes resident sessions and other-env agents", () => {
  const procs = [
    { pid: 100, cmd: "node hermes-managed-host.js run sc-coder" },
    { pid: 200, cmd: "hermes --tui --resume aify-some-resident" },
    { pid: 300, cmd: "node hermes-managed-host.js run other-team-agent" },
  ];
  const found = enumerateManagedSurvivors({ ownedAgentIds: ["sc-coder"], listProcesses: () => procs, readMarkers: () => [] });
  assert.deepEqual(found.deliveryLoops.map(p => p.pid), [100]);
});
```

- [ ] **Step 2: Run red.**
- [ ] **Step 3: Implement** — pure enumeration + a `reap` that delegates to `stopDaemon`, `defaultKillByPort`, `terminateProcessTree`, and `killByPid` (all existing). Injectable `listProcesses`/`readMarkers`/kill fns for tests. Mirror the agent-scoped / parent-`--aify-agent` safety from `reap-managed-claude.js`. Real `listProcesses` = `Win32_Process` (Windows) / `ps` (POSIX).
- [ ] **Step 4: Run green.** **Step 5: Commit.**

### Task 2.2: Shutdown teardown hook (graceful + supersede)

**Files:** Modify `mcp/stdio/server.js` (`shutdownWithStatus` ~:433-451, supersede branch ~:1659, sync `cleanupOnExit` ~:401-432). Test `mcp/stdio/tests/server-shutdown-teardown.test.js`.

- [ ] **Step 1: Failing test** — a `shutdownWithStatus` invocation on an environment bridge calls `reapManagedSurvivors` with the bridge's owned managed agent ids + `cwdRoots` after `TERMINAL_MANAGER.stopAll`. (Test the extracted `runManagedTeardown(deps)` seam.)
- [ ] **Step 2: Run red.**
- [ ] **Step 3: Implement** — extract `runManagedTeardown({ ownedAgentIds, cwdRoots, ... })`; call it in `shutdownWithStatus` (after `stopAll`) and ensure the supersede path routes through it; best-effort synchronous variant in `cleanupOnExit` (fire `spawnSync` kills). Only when `IS_ENVIRONMENT_BRIDGE`.
- [ ] **Step 4: Run green.** **Step 5: Commit.**

### Task 2.3: Boot-time survivor sweep

**Files:** Modify `mcp/stdio/server.js` (env-bridge boot, before `ensureSpawnLoop()` ~:1675/:2102). Test `mcp/stdio/tests/server-boot-reap.test.js`.

- [ ] **Step 1: Failing test** — on env-bridge boot, `reapOrphanedManagedSurvivors` reaps survivors whose owning bridge is NOT fresh in `bridge_instances`, and SKIPS any owned by a currently-live different bridge.
- [ ] **Step 2: Run red.**
- [ ] **Step 3: Implement** — query the server for managed agents in this env + their owning-bridge freshness; reap survivors with no live owner; gate on `IS_ENVIRONMENT_BRIDGE`. Log what it kills (no silent caps).
- [ ] **Step 4: Run green.** **Step 5: Commit.**

---

## WS3 — Deliverability-based status + queued-run backstop

### Task 3.1: Managed hermes joins the channel-sidecar-delivery gate

**Files:** Modify `service/routers/api_v2.py` (`_CHANNEL_SIDECAR_DELIVERY_RUNTIMES` :346; `has_live_worker` console branch :3253-3279; hermes branch :3325-3336). Test `service/tests/test_api_v2_regressions.py`.

- [ ] **Step 1: Failing test** — a managed hermes with a live console PTY row but a STALE channel-sidecar heartbeat computes `available` (not `online`); with both live → `online`.
- [ ] **Step 2: Run red** (today the console-presence branch manufactures `online`).
- [ ] **Step 3: Implement** — add `"hermes"` to `_CHANNEL_SIDECAR_DELIVERY_RUNTIMES` and make the `has_live_worker` console branch require `_has_live_channel_sidecar` for sidecar-delivery runtimes (so console/`-aify`/virtual-rpc presence alone can't set `online`). Verify no regression to the legit hermes channelEnabled path and to claude.
- [ ] **Step 4: Run green** (`pytest service/tests/`). **Step 5: Commit.**

### Task 3.2: Queued-run backstop reaper

**Files:** `service/routers/api_v2.py` (new `_reap_undeliverable_queued_runs`), `service/main.py` (wire into reconcile). Test `service/tests/test_api_v2_regressions.py`.

- [ ] **Step 1: Failing test** — a `queued` run whose target has no live claimer (no fresh channel-sidecar / no claiming bridge) and age > grace is failed with an actionable error + a `handoff`/reply mirror to the sender; a queued run with a live claimer is left alone.
- [ ] **Step 2: Run red.**
- [ ] **Step 3: Implement** `_reap_undeliverable_queued_runs(db)` selecting `status='queued'` past `queued_run_backstop_seconds` (new setting, default e.g. 180) whose target is not deliverable; mark failed, emit event, mirror to sender. Wire into `_run_dispatch_reconcile_once` (after requeue, before close-orphaned).
- [ ] **Step 4: Run green.** **Step 5: Commit.**

### Task 3.3: Fail-fast on send to a deaf target — **MOVED to WS5 (after Task 5.1)**

RESEQUENCED 2026-06-02: at send time a live-console/no-claimer managed agent is indistinguishable from a healthy wrapper-backed claimer that simply hasn't polled yet (claimers register lazily on first `/dispatch/claim`). Failing fast on channel-sidecar-row-absence breaks the normal lazy-claim delivery contract (7 `test_dispatch_channel_claim.py` tests). The disambiguator is the WS5 Task 5.1 **explicit claimer lease** (a positive "loop is a live claimer" signal). So 3.3 now runs as **Task 5.1b**: gate fail-fast on lease-absence, not sidecar-row-absence. The WS3.2 queued-run backstop already covers the in-flight stranded case after the 180s window (where the lazy-claim ambiguity has resolved).

### Task 3.4: Reaper covers the hermes triad failure

**Files:** `service/routers/api_v2.py` (`_reconcile_managed_worker_hygiene` :2270). Test same.

- [ ] **Step 1: Failing test** — a managed hermes with a stale channel-sidecar + an `attached` console row is reaped (console pointer cleared / row marked) the way claude's ghost path works.
- [ ] **Step 2: Run red** (hermes excluded today).
- [ ] **Step 3: Implement** — now that hermes is in `_CHANNEL_SIDECAR_DELIVERY_RUNTIMES`, confirm B1/B2 cover it; add any hermes-specific reason strings.
- [ ] **Step 4: Run green.** **Step 5: Commit.**

---

## WS4 — Cruft GC

### Task 4.1: Clear port/key markers on stop / kill-prior / agent-remove

**Files:** `mcp/stdio/hermes-endpoint.js` (new `clearGatewayMarkers(agentId, dir)`), `mcp/stdio/hermes-daemon.js` (`stopDaemon` calls it), `mcp/stdio/hermes-managed-host.js` (teardown calls it). Test `mcp/stdio/tests/hermes-endpoint.test.js`.

- [ ] **Step 1: Failing test** — `clearGatewayMarkers` removes `aify-hermes-port-<agent>` and `aify-hermes-key-<agent>`; `stopDaemon` invokes it.
- [ ] **Step 2: Run red.** **Step 3: Implement.** **Step 4: Run green.** **Step 5: Commit.**

### Task 4.2: Host-reported dead-PTY marking (PID-liveness reap of console rows)

**Files:** `mcp/stdio/server.js` (env-bridge periodic check marks its own `terminal_sessions` rows whose `process_id` is dead → POST a stop/reconcile), `service/routers/api_v2.py` (accept the host-reported terminal-dead signal). Tests on both sides.

- [ ] **Step 1: Failing tests** — (host) the bridge detects an `attached` row with a dead local `process_id` and reports it; (server) the report marks the row stopped + invalidates live-state.
- [ ] **Step 2: Run red.** **Step 3: Implement** — the server can't probe a remote PID, so the OWNING bridge reports liveness; reuse the existing terminal-control/heartbeat channel. **Step 4: Run green.** **Step 5: Commit.**

### Task 4.3: dispatch_runs pruner for tombstoned agents

**Files:** `service/routers/api_v2.py` (extend `_prune_terminal_history` :14839 or new `_prune_orphaned_dispatch_runs`; call from agent-remove :1296 and/or reconcile). Test same.

- [ ] **Step 1: Failing test** — terminal-status `dispatch_runs` for a tombstoned agent past a TTL are deleted; live runs untouched.
- [ ] **Step 2: Run red.** **Step 3: Implement.** **Step 4: Run green.** **Step 5: Commit.**

---

## WS5 — Event-driven turn/liveness signals (replace second-based switches)

> Highest-risk workstream (prior feedback-loop regressions). Each task is behaviour-tested and additive: introduce the event signal, prove it, THEN lengthen/remove the time window — never remove a window before its replacement is green. Keep ONE long wall-clock backstop.

### Task 5.1: Explicit delivery-loop claimer lease

**Files:** `mcp/stdio/hermes-managed-host.js` (register/clear lease), `service/routers/api_v2.py` (lease store + `_has_live_claimer` uses lease, not 180s inference). Tests both sides.

- [ ] **Step 1: Failing test** — `online`/deliverability is driven by an explicit lease set on loop start and cleared on loop exit/teardown, independent of the 30s heartbeat cadence; a cleanly-exited loop is immediately non-deliverable.
- [ ] **Step 2: Run red.** **Step 3: Implement** — loop POSTs a `claimer-acquire` on ready (Task 1.4) and `claimer-release` in teardown; server records it; `_has_live_channel_sidecar`/new `_has_live_claimer` prefers the lease, with `CHANNEL_SIDECAR_STALE_SECONDS` only as the backstop if a lease release was missed. **Step 4: Run green.** **Step 5: Commit.**

### Operator-confirmed bugs this workstream MUST fix (acceptance criteria)
- **Bug A (false-working):** a managed hermes that finishes its turn stays `working` (turn_busy only decays on the 120s timer). Acceptance: on a real turn-end event the agent flips to idle/`online` immediately (no 120s wait).
- **Bug B (stranded queue, downstream of A):** `comms_send` to that agent queues as next-turn work "because the system thinks he's working", and it never delivers because the turn never appears to end. Acceptance: after the turn-end event clears `turn_busy`, any queued run for that agent is delivered on the next claim (regression test: turn-start → send (queues) → turn-end → queued run becomes delivered, agent not left `working`). For a genuinely deaf target (no live claimer) the WS3 fail-fast applies instead of an indefinite queue.

### Task 5.2: Event-driven turn-start/turn-end for hermes

**Files:** `mcp/stdio/hermes-managed-host.js` / hermes plugin hooks (`pre_llm_call`/`post_llm_call` or gateway WS `run.*`), `service/routers/api_v2.py` (`turn_busy` set/clear on events; `TURN_BUSY_STALE_SECONDS` demoted to long backstop). Tests both sides.

- [ ] **Step 1: Failing test** — `turn_busy` is set on a real turn-start event and cleared on a real turn-end event; the 120s window no longer drives the normal transition (only a long backstop, e.g. 15m, if an end event is dropped).
- [ ] **Step 2: Run red.** **Step 3: Implement** — wire the turn-end signal (the missing piece) from the hermes gateway/plugin; clear `turn_busy` on it; lengthen `TURN_BUSY_STALE_SECONDS` usage to backstop-only. Cover dispatch, autonomous, and direct-typed turns. **Step 4: Run green** + run the existing feedback-loop tests (`claude-channel-feedback-loop.test.js` analogue) to prove no self-reinforcing re-pulse. **Step 5: Commit.**

### Task 5.3: Collapse remaining second-based windows to backstops

**Files:** `service/routers/api_v2.py` (constants table in spec §WS3/5). Test same.

- [ ] **Step 1: Failing test** — for each window now backed by an event (claimer lease, turn events, bridge/WS disconnect), the normal transition is event-driven and the time constant only fires when its event is missing.
- [ ] **Step 2: Run red.** **Step 3: Implement** — adjust each constant's role; keep one long wall-clock ceiling. **Step 4: Run green** (full `pytest`). **Step 5: Commit.**

---

## WS6 — Docs / skills / harnesses / integrations

### Task 6.1: install.sh + harness docs

- [ ] Update `install.claude.md` / `install.hermes.md` for the loop health-gate and restart-teardown behavior. Commit.

### Task 6.2: Skills (both mirrors) + KNOWN_ISSUES + DECISIONS + README

- [ ] `.claude/skills/aify-comms-debug/SKILL.md` + `.agents/` mirror: replace the "Many hermes.exe" / "Team stranded" / "Agent online but no worker" entries with the new lifecycle-owner + restart-teardown behavior; add "deaf target fails fast" and "queued-run backstop".
- [ ] `KNOWN_ISSUES.md`: close the items this batch resolves (#172 autonomous-hermes if WS5 covers it; the gateway-liveness residual; the daemon/loop split). `DECISIONS.md`: record the triad-supervisor + restart-teardown rationale. `README.md` managed-modes section: state restart = clean slate. Keep mirrors in sync. Commit.

### Task 6.3: Final regression + deploy + live-validation

- [ ] Full `node --test` (confirm only the 6 known pre-existing hermes-adapter glob failures) + full `pytest`.
- [ ] Commit; rebuild container (`docker compose up -d --build`); `curl /health`.
- [ ] Restart the sc-* hermes wrappers; operator live-validation matrix: (a) restart aify-comms → ZERO managed survivors; (b) fresh spawn → loop health-gated, console live + input works; (c) send to deaf agent → fail-fast; (d) no queued pileups; (e) status reflects real turns, no flap. Document results in memory.

---

## Self-review (against spec)

- **Spec coverage:** WS1↔defects 2/3/6(loop side); WS2↔defect 6 + HARD req; WS3↔defects 1/4/5(status) + deaf-fast decision; WS4↔defects 5(rows)/7; WS5↔req 5; WS6↔req 6. All six requirements mapped.
- **Decisions baked:** fail-fast (3.3), env-scoped teardown (2.1 enumeration), WS5-in-batch (sequenced same branch).
- **Type consistency:** ready-marker helpers (`writeLoopReady`/`loopReadyFresh`/`clearLoopReady`) used 1.4→1.5; `reapManagedSurvivors`/`enumerateManagedSurvivors` used 2.1→2.2→2.3; `_has_live_claimer`/`_has_live_channel_sidecar` consistent 3.1→3.3→5.1; `clearGatewayMarkers` 4.1 reused in 1.3 teardown.
- **No placeholders:** every task has a concrete test + file:line target.
- **Visible-TUI HARD req preserved:** WS1 health-gate refuses to show a TUI that can't receive work; WS4.2 reaps frozen consoles; no headless paths introduced.
