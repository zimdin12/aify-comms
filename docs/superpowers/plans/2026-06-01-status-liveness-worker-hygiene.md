# Status Liveness & Worker Hygiene Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make agent status server-authoritative and truthful by giving every long-lived bridge a real liveness signal, then build correctness (ghost-console reaping, event-driven freshness, collision-proof hermes ports) and a quieter chat on top of it.

**Architecture:** Today status conflates *"recently active"* with *"currently alive"* — `bridge_instances.last_seen` only advances on activity (a claim, a turn, a handle change), so an idle-but-alive worker looks dead and the system has accreted compensating carve-outs. This plan separates two orthogonal axes: **liveness** (is the worker process alive + connected? — one unconditional heartbeat) and **activity** (is it working a turn? — turn_busy / active run / transcript). Status becomes `f(liveness, activity, env, policy)`, every consumer reads one derivation, and several carve-outs get deleted rather than carried forward.

**Tech Stack:** Python 3.11 / FastAPI / aiosqlite (service, in container — rebuild after changes), Node ESM stdio bridges (`mcp/stdio/`, host-side — restart wrapper after changes), vanilla JS dashboard (`service/dashboard.html`, in container — rebuild). Tests: `python -m pytest service/tests/`, `node --test mcp/stdio/tests/`.

**Supersedes:** `docs/superpowers/plans/2026-05-31-event-driven-status-and-worker-hygiene.md` (predates the root-cause clarity in [[status-cache-freeze-rootcause]]). Builds on the already-shipped 60s status self-heal sweep (commit 1359a65) — that sweep is the safety net; this plan makes status correct and fresh.

**Operator constraints (must honor):** never run opencode tests live; never rebuild the container mid-edit (it COPYs the working tree — commit first); `mcp/stdio/` changes need a wrapper restart, not a rebuild; commits end with the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer; keep cross-agent messages terse; `windowsHide:true` on all child spawns; visible-TUI-in-dashboard is a hard requirement; confirm before destructive/outward actions.

---

## Workstream order (dependencies)

```
A (liveness heartbeat)  ── foundation, do first
  ├─► B (console↔worker lifetime: ghost-row reaper + headless-orphan reaper + visible-TUI status rule)
  ├─► A' (delete carve-outs)     safe only once liveness is real
  └─► C (event-driven + WS push) freshness on top of correct values
D (chat spam)      independent, any time
E (hermes ports)   independent, do early (recurring live pain)
```

Land **A**, validate live, then **B + A'**, then **C**. **D** and **E** are independent and can interleave. Each workstream ends green + committed before the next.

---

## File map

| File | Workstream | Responsibility |
|------|-----------|----------------|
| `mcp/stdio/liveness-heartbeat.js` *(new)* | A | Unconditional periodic liveness beat helper (mirrors `turn-busy-heartbeat.js`) |
| `mcp/stdio/server.js` | A | Start liveness beat for resident MCP bridge |
| `mcp/stdio/claude-channel.js` | A | Start liveness beat for channel-sidecar |
| `mcp/stdio/hermes-managed-host.js` | A | Start liveness beat for managed-hermes delivery loop |
| `service/routers/api_v2.py` | A, A', B, C | Heartbeat endpoint accepts liveness beat; liveness-based status; ghost reaper; event invalidation |
| `service/main.py` | B | Wire ghost reaper into the 60s reconcile pass |
| `service/ws.py` + `service/routers/api_v2.py` | C | Broadcast recomputed status on state-changing events |
| `service/dashboard.html` | C, D | Consume pushed status; gate the chat run-note |
| `mcp/stdio/claude-channel.js` / `hermes-channel.js` / `hermes-managed-host.js` | D | Stop emitting boilerplate delivery `summary` |
| `mcp/stdio/hermes-endpoint.js` | E | Collision-proof `resolveGatewayPort` (cross-agent uniqueness) |

---

## Workstream A — Unconditional liveness heartbeat (FOUNDATION)

**Why:** `bridge_instances.last_seen` must mean "alive now," not "did something recently." Each long-lived bridge process emits a fixed-interval beat regardless of work. Then `_has_live_channel_sidecar` / `_resident_bridge_is_fresh` become true liveness signals.

**Design:** New `liveness-heartbeat.js` exporting `startLivenessHeartbeat({ intervalMs, beat })` — a thin unconditional timer (sibling to `turn-busy-heartbeat.js`, which is conditional on `isActive`). Each bridge passes a `beat` that POSTs `/agents/{id}/heartbeat` with its own `bridgeId`/`bridgeKind` and `liveness:true`. Server-side, that endpoint already upserts `bridge_instances.last_seen`; add an explicit `liveness` path that updates `last_seen` (and only `last_seen`) without requiring turn/handle fields. Interval 30s; freshness window 75s (2.5×) so one dropped beat tolerated.

### Task A1: liveness-heartbeat.js helper + test

**Files:**
- Create: `mcp/stdio/liveness-heartbeat.js`
- Test: `mcp/stdio/tests/liveness-heartbeat.test.js`

- [ ] **Step 1: Write the failing test**

```js
// mcp/stdio/tests/liveness-heartbeat.test.js
import { test } from "node:test";
import assert from "node:assert/strict";
import { startLivenessHeartbeat } from "../liveness-heartbeat.js";

test("beats immediately and then on the interval; stop() halts beats", async () => {
  const calls = [];
  const stop = startLivenessHeartbeat({
    intervalMs: 20,
    beat: async () => { calls.push(Date.now()); },
  });
  await new Promise((r) => setTimeout(r, 75)); // ~ first + 3 interval beats
  stop();
  const after = calls.length;
  await new Promise((r) => setTimeout(r, 60));
  assert.ok(after >= 3, `expected >=3 beats, got ${after}`);
  assert.equal(calls.length, after, "no beats after stop()");
});

test("a throwing beat never crashes the timer", async () => {
  let n = 0;
  const stop = startLivenessHeartbeat({
    intervalMs: 15,
    beat: async () => { n += 1; throw new Error("boom"); },
  });
  await new Promise((r) => setTimeout(r, 60));
  stop();
  assert.ok(n >= 2, "kept beating despite throws");
});
```

- [ ] **Step 2: Run it — expect FAIL** (`node --test mcp/stdio/tests/liveness-heartbeat.test.js` → "Cannot find module").

- [ ] **Step 3: Implement**

```js
// mcp/stdio/liveness-heartbeat.js
// Unconditional liveness beat. Distinct from turn-busy-heartbeat.js (which is
// gated on isActive): this fires for as long as the process lives so the
// service can treat bridge last_seen as a true "alive now" signal. See
// docs/superpowers/plans/2026-06-01-status-liveness-worker-hygiene.md.
export function startLivenessHeartbeat({ intervalMs = 30000, beat } = {}) {
  if (typeof beat !== "function") throw new Error("startLivenessHeartbeat: beat required");
  let stopped = false;
  const tick = async () => {
    if (stopped) return;
    try { await beat(); } catch { /* never let a failed beat kill the timer */ }
  };
  // beat once immediately so a freshly-started process is live without waiting a full interval
  void tick();
  const timer = setInterval(tick, Math.max(1000, intervalMs));
  if (typeof timer.unref === "function") timer.unref();
  return function stop() { stopped = true; clearInterval(timer); };
}
```

- [ ] **Step 4: Run test — expect PASS.**
- [ ] **Step 5: Commit** (`feat(bridge): unconditional liveness-heartbeat helper`).

### Task A2: server-side liveness path on the heartbeat endpoint + test

**Files:**
- Modify: `service/routers/api_v2.py` — the `/agents/{agentId}/heartbeat` handler (find it; it already updates `bridge_instances`). Accept an optional `liveness: bool`/`bridgeKind` and, when set, upsert `bridge_instances(id, agent_id, bridge_kind, last_seen)` with `last_seen=_now()` — touching ONLY `last_seen` (never clearing `superseded_by`, never altering turn_busy).
- Test: `service/tests/test_api_v2_regressions.py`

- [ ] **Step 1: Write failing test** — register/seed a channel-sidecar bridge with an old `last_seen`; POST `/agents/<id>/heartbeat` with `{bridgeId, bridgeKind:'channel-sidecar', liveness:true}`; assert the bridge row's `last_seen` advanced and `_has_live_channel_sidecar(db, id)` is now True. (Mirror the seeding style around `test_api_v2_regressions.py:4223`.)
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** the `liveness` branch in the heartbeat handler. Reuse the existing bridge-upsert SQL pattern (search the handler for the current `INSERT INTO bridge_instances ... ON CONFLICT`). Do NOT touch `superseded_by`.
- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit** (`feat(status): heartbeat endpoint accepts unconditional liveness beat`).

### Task A3: wire liveness beat into the three bridges

**Files:** `mcp/stdio/claude-channel.js`, `mcp/stdio/server.js`, `mcp/stdio/hermes-managed-host.js`

- [ ] **claude-channel.js:** in `pollLoop` startup (near line 340), after the bound agentId is known, start `startLivenessHeartbeat({ intervalMs: 30000, beat: () => httpCall("POST", \`/agents/${encodeURIComponent(agentId)}/heartbeat\`, { bridgeId: channelBridgeId(agentId), bridgeKind: "channel-sidecar", liveness: true }) })`; call its `stop()` on loop exit. (agentId can rotate per-poll via `readBoundAgentId`; resolve it the same way the poll does, or restart the beat when it changes.)
- [ ] **server.js:** alongside `__stopTurnBusyHeartbeat` (line 269), add a liveness beat posting `{ bridgeId: BRIDGE_INSTANCE_ID, bridgeKind: "resident", liveness: true }` for the resident MCP bridge; stop it in the existing shutdown path (near line 360).
- [ ] **hermes-managed-host.js:** in the delivery loop (`runDeliveryLoop`), start a liveness beat for the managed-hermes bridge id; stop on loop exit.
- [ ] **Verify:** `node --check` each file; `node --test mcp/stdio/tests/`. Live-verify after wrapper restart: an idle managed-claude agent stays `online` (sidecar `last_seen` keeps advancing). Commit (`feat(bridge): emit liveness beat from sidecar, resident, and managed-hermes bridges`).

### Task A' (after A is live-validated): delete the compensating carve-outs

These existed only because liveness was unreliable. Remove each behind its own test run; keep the dispatch-claim self-heal of a *genuinely superseded* sidecar only if a test proves it still needed.

- [ ] Re-evaluate and simplify in `service/routers/api_v2.py`: the channel-sidecar claim-path self-heal (un-supersede on claim), the absolute complementary-pair protection in `_record_bridge_registration`, and the "idle resident accepts a live sidecar" branch in `_resident_bridge_is_fresh` (line 1722). For each: write/keep a test asserting the *behaviour* (idle resident shows online; live sidecar not falsely superseded), then remove the special-case and confirm the test still passes via the liveness signal. If a removal breaks a test that encodes real intent, keep that carve-out and document why. Commit per removal.

---

## Workstream B — Managed console↔worker lifetime coupling (the core hygiene fix)

**Why (two live incidents, both observed on sc-claude):**
1. **Ghost terminal row** — a managed wrapper dies but its `terminal_sessions` row stays `attached`, so the dashboard shows a phantom console.
2. **Headless orphan worker (MORE IMPORTANT)** — the opposite: the console PTY *stops* but the worker tree it spawned (`claude.exe` + the `claude-channel.js` channel-sidecar + MCP children) keeps running. The sidecar keeps claiming, so the agent reports `online` and is reachable — **with no visible console**. This violates the visible-TUI hard requirement, is the "lost/background session" failure mode, and causes proliferation (the next send lazy-autostarts *another* console on top of the orphan). Observed: sidecar 3s-fresh while all `terminal_sessions` were `stopped`; three consoles spawned+stopped in ~30 min over one surviving worker.

**Invariant to enforce:** for a managed agent, **worker lifetime == console-PTY lifetime**. A managed agent is either *online and console-bound*, or *fully down* (→ `available`, lazy-autostartable with a fresh visible TUI). There is no "alive worker, no console" state. Safe only on top of Workstream A (liveness must be trustworthy before we reap or kill).

**Design — three coupled pieces:**
- **Host-side (authoritative): kill the worker tree when its PTY closes.** When a managed console PTY exits/closes, the terminal manager must terminate the entire spawned child tree (`cmd → bash → claude.exe → node sidecar/MCP`). On Windows, closing a node-pty does NOT reliably signal the descendant tree, which is how these orphans arise. Use a tree-kill (e.g. `taskkill /T /PID <ptyRootPid>` or `process.kill` over the recorded child group) on PTY close. Defense-in-depth: the channel-sidecar self-exits if it detects its controlling parent (`claude.exe` / the PTY) is gone.
- **Server-side reaper (backstop): `_reconcile_managed_worker_hygiene(db)`** in the reconcile loop. For each MANAGED agent, reconcile the two divergences: (a) **ghost row** — `terminal_sessions` `attached`/`running`/`starting` but no live worker (no fresh sidecar/wrapper liveness, stale beyond the offline window) → set terminal `exited`, clear `consoleTerminal` runtime_state, broadcast `terminal_exited`; (b) **orphan worker** — a fresh liveness sidecar but NO live console PTY beyond a grace window → request the host to kill the orphan worker (emit a `kill_orphan_worker` control the wrapper/host honors) and mark the agent `available`. Gate both strictly on the Workstream-A liveness verdict + a grace window so a transiently-restarting console is never reaped.
- **Status rule:** managed `online` requires a live console PTY **and** a live sidecar (they are now coupled). A live sidecar with no PTY is the invalid orphan state → reaped → `available`. This refines status-F1 (which required only the sidecar).

### Task B1: server reaper — ghost terminal row + test
**Files:** Create `_reconcile_managed_worker_hygiene` in `service/routers/api_v2.py`; tests in `service/tests/test_api_v2_regressions.py`.
- [ ] **Step 1: Failing test** — seed a MANAGED agent with NO live bridge and a stale `attached` terminal; call the reaper; assert terminal→`exited` + `terminal_exited` event. Second test: MANAGED agent WITH fresh liveness + a live `attached` terminal keeps it (not reaped).
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** the ghost-row half, modelled on `_reconcile_stale_managed_terminals_for_resident_agents` (api_v2.py:2147), gated on the liveness verdict + terminal staleness.
- [ ] **Step 4: Run — expect PASS.** **Step 5: Commit.**

### Task B2: server reaper — orphan worker (alive sidecar, no console) + test
**Files:** same function (extend); tests in `service/tests/test_api_v2_regressions.py`.
- [ ] **Step 1: Failing test** — reproduce the sc-claude incident: MANAGED agent with a FRESH channel-sidecar (`_has_live_channel_sidecar` True) but ALL `terminal_sessions` `stopped` beyond the grace window. Assert the reaper (a) emits the `kill_orphan_worker` control/event and (b) the agent's computed status is `available`, NOT `online`. Second test: fresh sidecar + a live `attached` terminal → stays `online`, no kill.
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Implement** the orphan-worker half + the refined status rule (managed `online` requires live PTY AND live sidecar). Update any test that previously enshrined "managed online from sidecar alone" to also stamp a live terminal (the `_stamp_live_channel_sidecar` helper pattern — add a `_stamp_live_console` companion).
- [ ] **Step 4: Run — expect PASS.** **Step 5: Commit** (`fix(status): managed online requires a live console; reap headless orphan workers`).

### Task B3: host-side — kill worker tree on PTY close + sidecar self-exit
**Files:** the host-side terminal/PTY manager in `mcp/stdio/` (find where managed PTYs are spawned/closed — search for `node-pty`/`spawn`/the `claude-aify` launch + the terminal-stop path; `_request_stop_agent_terminals` on the server triggers a `stop` control the host honors); `mcp/stdio/claude-channel.js` (self-exit guard).
- [ ] **Step 1:** On managed PTY close/exit (and on the `stop`/`kill_orphan_worker` control), tree-kill the spawned descendants (record the PTY root pid at spawn; `taskkill /T /F /PID` on Windows, tree kill elsewhere). Add a node test that the close handler invokes the tree-kill with the recorded root pid (inject the killer for the test).
- [ ] **Step 2:** In `claude-channel.js`, add a periodic guard: if the controlling parent process (`process.ppid` / the `claude.exe` that launched it) is gone, stop the poll loop + exit. Test the guard predicate in isolation.
- [ ] **Step 3:** `node --check` + `node --test`; live-verify: stop sc-claude's console → its `claude.exe`+sidecar host processes are gone within the grace window (no headless survivor); next send lazy-autostarts exactly ONE fresh visible console. **Commit** (`fix(bridge): bind managed worker lifetime to its console PTY (no headless orphans)`).

### Task B4: wire reaper into the reconcile loop
**Files:** `service/main.py` (`_run_dispatch_reconcile_once`).
- [ ] Import + `await _reconcile_managed_worker_hygiene(db)` before `db.commit()`; extend the result dict (`"managed_ghost_rows_reaped"`, `"orphan_workers_reaped"`). Add a test asserting `_run_dispatch_reconcile_once()` handles a seeded ghost row + a seeded orphan (mirror `test_periodic_reconcile_refreshes_expired_live_states`). Commit.

---

## Workstream C — Event-driven invalidation + WS status push

**Why:** the 60s sweep (shipped) bounds staleness to 60s; this makes status fresh within event latency and removes the dashboard's reliance on a throttleable client timer.

**Design:** (1) ensure every state-changing path calls `_invalidate_agent_live_state` (api_v2.py:3248) + recomputes + broadcasts `agent_status` (the `ws.broadcast("agent_status", {...})` pattern at api_v2.py:10722). Audit: turn-start/turn-end, dispatch claim/complete/fail, terminal start/exit, bridge register / liveness-lost, env up/down. (2) Dashboard: on the `agent_status` WS event, update only that agent's row from the payload instead of scheduling a full `refreshDashboard()`. Keep the client `setInterval` as a slow safety net (e.g. raise to 60s) — but correctness no longer depends on it.

### Task C1: server emits agent_status on each state change
- [ ] For each event path missing it, add invalidate + recompute (`_compute_agent_status`) + `ws.broadcast("agent_status", {...})`. One small test per path (or a representative subset) asserting a broadcast is emitted (the test harness can stub `ws`). Commit incrementally.

### Task C2: dashboard consumes pushed status granularly
**Files:** `service/dashboard.html` (WS event handler; search the `onmessage`/event switch).
- [ ] On `agent_status`, mutate the in-memory agent and re-render just that row (no full refresh). Raise the `setInterval` fallback to 60s. Manual verify: background the tab, change an agent's state, foreground — status is already correct (pushed), not stale. Commit (`feat(dashboard): apply pushed agent_status updates without full refresh`).

---

## Workstream D — Silence routine resident-delivery chat spam

**Why:** `service/dashboard.html:6231-6233` renders a `<details class="chat-run-note">` whenever `deliveryDetail` is non-empty; for routine resident/console deliveries that detail is the boilerplate `run.summary` = `"Delivered to Claude resident session; awaiting explicit reply"`. Useful for failures/steers, pure noise for routine deliveries. The "awaiting reply" state is already surfaced via the chat presence `· awaiting reply` note.

**Design (two layers):** (1) **Dashboard gate (primary):** only render the run-note for *noteworthy* runs — `failed`/`cancelled`, steered/steer-failed, queued-behind, or a present `resultMessageId` — OR when the detail is genuinely informative (not the boilerplate). (2) **Source trim (optional, cleaner):** stop writing the boilerplate prose into `summary` in `markDispatchDelivered` (claude-channel.js:289, hermes-channel.js, hermes-managed-host.js) — the `delivered` status + require_reply already encode "awaiting reply"; leave `summary` empty for routine deliveries so the Runs audit view is clean too.

### Task D1: dashboard run-note gating + helper test
**Files:** `service/dashboard.html` (helper near 6200 + render at 6231); add a JS unit test in the existing dashboard test harness (`service/new_dashboard/app.test.mjs` pattern, or a small `node:test`).
- [ ] **Step 1:** add `function isNoteworthyDeliveryRun(deliveryRun)` returning true only for failed/cancelled/steer/queued-behind/reply-present. Failing test: a routine `delivered` + boilerplate-summary run → `false`; a `failed` run → `true`.
- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3:** implement it; change the `runDetail` condition (6231) to `relatedRun && isNoteworthyDeliveryRun(deliveryRun)`.
- [ ] **Step 4: Run — expect PASS.** Manual verify: routine ACK replies no longer show the trailing "Delivered to … awaiting explicit reply" note; a failed dispatch still does.
- [ ] **Step 5: Commit** (`fix(dashboard): only show the chat run-note for noteworthy deliveries`).

### Task D2 (optional): stop emitting boilerplate summary at source
- [ ] In the three bridges' `markDispatchDelivered`, set `summary: ""` for routine `require_reply` deliveries (keep meaningful summaries for failures). Bridge change → wrapper restart. Update the two `markDispatchDelivered` tests + the `aify-comms-debug` skill note that references the old string (both skill mirrors). Commit.

---

## Workstream E — Collision-proof hermes gateway ports

**Why (live incident):** `comms-senior-dev` and `graph-hermes-tl` both hash to base port 9341. `resolveGatewayPort` (hermes-endpoint.js) (a) returns a persisted port without re-checking it's free/uniquely-ours, and (b) probes only against instantaneous `portFree`, so two agents resolving before either gateway binds both persist 9341 → flapping "gateway websocket connection failed". (Worked around live by pinning comms-senior-dev to 9342.)

**Design:** make a persisted/assigned port valid only if it is **bindable now AND not already claimed by another agent's port file**. On conflict, re-probe forward for a port that is both free and unclaimed, then persist. This makes assignments stable AND globally unique across agents on the host.

### Task E1: cross-agent-unique resolveGatewayPort + tests
**Files:** `mcp/stdio/hermes-endpoint.js`; tests in `mcp/stdio/tests/hermes-gateway-port.test.js`.
- [ ] **Step 1: Failing tests** (extend existing file):
  - "two agents that share a base port get distinct persisted ports even when both resolve before binding" — call `resolveGatewayPort(a)` then `resolveGatewayPort(b)` with `portFree: async () => true` (nothing bound yet) and a shared `tempDir`; assert `pa !== pb`. (Today both return the base port → FAILS.)
  - "a persisted port already claimed by another agent's file is re-probed" — pre-write `aify-hermes-port-other = 9341` in tempDir, then `resolveGatewayPort('mine')` whose base is 9341 → must NOT return 9341.
  - Keep the existing persistence/reuse + range tests passing.
- [ ] **Step 2: Run — expect the two new tests FAIL.**
- [ ] **Step 3: Implement.** Add a helper that reads all `aify-hermes-port-*` files in `tempDir` (excluding the current agent's) into a claimed-set. A candidate is acceptable iff `await portFree(candidate)` **and** not in the claimed-set. Apply this check to BOTH the persisted-reuse branch (re-probe if the persisted port is no longer acceptable) and the probe loop. Persist the chosen port. Keep the 8642–9641 range + FNV-1a base.
- [ ] **Step 4: Run — expect PASS** (all gateway-port tests).
- [ ] **Step 5: Commit** (`fix(hermes): make gateway port assignment free AND cross-agent unique`).

### Task E2: live rollout
- [ ] After E1 deploys (bridge change → respawn affected hermes agents), remove any duplicate persisted port files so colliding agents re-resolve cleanly. Verify each managed-hermes agent's gateway binds a distinct port and its delivery-loop WS connects (no "gateway websocket connection failed"). Document the deterministic-pin workaround + the real fix in `.claude/skills/aify-comms-debug/SKILL.md` (+ `.agents/` mirror).

---

## Rollout & safety

- **Per workstream:** full `python -m pytest service/tests/` + `node --test mcp/stdio/tests/` green before commit. Backend (`service/`, `dashboard.html`) changes → commit then `docker compose up -d --build` + `curl :8800/health`. Bridge (`mcp/stdio/`) changes → `node --check` + restart the affected wrappers/agents.
- **A before B/A'/C** — do not reap consoles or delete carve-outs until liveness is live-validated on at least one idle managed agent.
- **Live validation matrix:** idle managed claude stays `online` *with its console attached*; idle resident stays `online`; dead managed wrapper → ghost console row reaped + agent `available`; **headless orphan (alive sidecar, no PTY) → worker killed + agent `available`, next send autostarts exactly ONE fresh visible console**; busy agent → `working`; two same-base-port hermes agents → distinct gateways. (Do NOT spin up opencode.)

## Self-review checklist (run before handing off)
- [ ] Spec coverage: all issues from the 2026-06-01 discussion have tasks — liveness (A), console↔worker lifetime incl. headless-orphan + visible-TUI rule (B), event-driven freshness (C), chat spam (D), hermes ports (E).
- [ ] No placeholders; every code step shows code or names the exact symbol+line to edit.
- [ ] Names consistent: `startLivenessHeartbeat`, `_reconcile_dead_managed_terminals`, `isNoteworthyDeliveryRun`, `resolveGatewayPort`.
- [ ] Dependencies honored (A→B/A'/C; D,E independent).
