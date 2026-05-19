# Dashboard Architecture Plan (proposal — operator approval required)

Status: **DRAFT for operator decision.** No code/rewrite on this branch; the
build happens on a dedicated next branch after approval. Co-authored:
`comms-tech-lead` (service/dashboard) + `comms-senior-dev-pi` (bridge/runtime/MCP
— see §6, its independent critique).

## 0. Architecture (as-is) — canonical mental model

```
            Operator
               │  (browser; chat = convenience over the reliable path)
               ▼
   ┌─────────────────────────────┐        aify-comms container
   │  Dashboard UI (dashboard.html)│  ───┐
   └─────────────────────────────┘      │  FastAPI + SQLite (control plane,
               │  HTTP/WS  /api/v1       │  the contract: agents, runs,
               ▼                         │  sessions, artifacts, status)
   ┌─────────────────────────────┐  ◀───┘
   │  Service (service/, MCP SSE)│
   └─────────────────────────────┘
               ▲  spawn-requests / heartbeats / dispatch / turn-busy
               │  (HTTP)
   ┌─────────────────────────────┐   host(s): Windows / WSL / Linux
   │  Bridge  mcp/stdio/server.js │   (the `aify-comms` process you run)
   └─────────────────────────────┘
        │                         │
        │ (1) Managed dispatch    │ (2) Dashboard Console (optional)
        │     NATIVE integration  │     interactive `*-aify` wrapper
        ▼                         ▼     in a node-pty PTY, streamed to UI
   codex app-server /        codex-aify / pi-aify /
   pi --mode rpc /           hermes-aify / claude-aify
   Claude channel / hermes        │
        │                         └── human watches / drives the CLI
        ▼
   the agent runtime does the work; result flows back over the
   native path → service → dashboard. Messaging NEVER depends on the PTY.
```

**Two execution paths (the critical distinction):**
1. **Managed dispatch** — primary. Bridge drives the runtime via its native
   API/RPC (codex app-server, `pi --mode rpc`, Claude channel, hermes). All
   chat/`comms_send` traffic and results flow here. Reliable, runtime-correct.
2. **Console (PTY)** — optional, separate. Bridge launches the interactive
   `*-aify` wrapper in a real PTY and streams it to the browser for
   watch/hand-drive only. Not a data path; messaging is decoupled from it.

**Resident vs managed:** running `*-aify --aify-agent <id>` by hand registers
that live CLI as the *resident owner* of the identity (messages route to the
visible session); managed = the bridge owns lifecycle. Same identity record.

**Stable contracts (do not rebuild):** the HTTP API, MCP layer, and the
bridge↔service protocol. The rebuild is the **UI layer only**.

## 1. Problem (evidence, not vibes)

`service/dashboard.html` is a single ~7k-line file with all JavaScript and CSS
inline, served verbatim (COPY'd into the container image — no build step). There
are no UI tests and no componentization. Consequences observed this branch:

- Every edit is global and untyped; small changes silently regress others.
- No automated UI verification → regressions surface only via operator testing.
- The status-display logic, console rendering, chat, spawn, settings all share
  one scope — fixes in one area destabilize another.

This is a genuine architecture problem. It is **not** a reason to discard the
HTTP API or MCP layer, which are stable, tested (142 python + 28 stdio files),
and the contract everything depends on.

### Known issues the rebuild must explicitly solve

- **Multi-tab / multi-PC state desync.** Two dashboards open → one tab acts,
  the other shows stale state (e.g. a stopped terminal still showing cached
  output next to a "Start console" button). The monolith refreshes per-tab on
  a timer/ws fallthrough with no shared client state model. The componentized
  rebuild must use **websocket-driven shared state** as the single client
  source of truth so all tabs converge without manual refresh, and derive
  console actions/body from one consistent state object (no "console + Start"
  contradiction).
- **Status/label/dot consistency** must be structural: one canonical status
  value drives the text, the dot, and any badges (we just had to hand-fix the
  dot disagreeing with the status — a class of bug componentization prevents).
- **Bulk actions** (multi-select agents/sessions/runs) — a first-class feature
  of the new component model, not bolted onto the monolith.
- **Resident-session working detection.** Turn-busy status is bridge-driven
  (managed only). Resident sessions (operator-run `*-aify`) have no bridge
  turn; accurate "working" there needs the resident wrapper to emit its own
  busy signal (start/end of turn) into the same turn-busy contract. Follow-up,
  not a heuristic — captured so it isn't hot-patched.
- **Status taxonomy as a designed contract.** `working` / `active` / `idle` /
  `offline` now comes from the live-state engine: a real active run or fresh
  turn-busy heartbeat means `working`; attached-but-runless Console is
  `active`; stopped/failed Console terminals are cleared as current bindings
  and remain historical only. A working agent's dot briefly pulses orange when
  its terminal emits output; this is a visual activity hint, not a separate
  status. A future `blocked` state still needs a real contract before
  implementation. Specifically, a fixed 10s console-silence threshold is too
  aggressive (agents legitimately reason / tool-call / wait on I/O for far
  longer with no console output → false "blocked", same class as the reverted
  heuristics). If added, base it on the authoritative turn-busy signal + a
  generous, configurable quiescence window, defined in the contract — not a
  hardcoded 10s on the monolith.
- **Session-id normalization / wording.** All runtimes have a native session
  concept but it becomes known at different times (codex/claude/opencode/
  hermes ~at register/handle; pi/omp at first completed run). Surface this as
  "session pending — captured on first run" rather than "no handle", and
  normalize how each runtime's id is displayed/repaired.

## 2. Options

**A. Big-bang React rewrite (rebuild dashboard from scratch).**
- Pros: clean component model, types, ecosystem, bulk-actions/UX done "properly".
- Cons: highest risk for a serious in-production control plane; freezes feature
  work for weeks; reintroduces just-fixed behavior (status, console, decouple,
  retention); a long window where the new UI lags the old in coverage. Strongly
  not recommended as the first move.

**B. Staged modernization (recommended).** Same API/MCP, incremental:
1. **Add a build step + extract** inline JS/CSS into ES modules/components with
   **zero behavior change** (mechanical, diff-verifiable, regression-tested).
2. **Add UI smoke/contract tests** (Playwright or similar) covering the flows we
   keep breaking: status display, console decouple, chat send, spawn, settings.
   This is the single biggest lever against "changes introduce issues".
3. **Introduce a component layer incrementally**, page-by-page, behind the
   unchanged API. Each page migrates + ships independently with its tests.
4. Build bulk-actions and the missing UX **on the componentized base**, not the
   monolith.

**C. Do nothing / keep patching.** Rejected — the friction is real and compounding.

## 3. Recommendation

**Option B.** Stage it behind frozen contracts. Rationale: keeps the product
working and shippable throughout, makes each step verifiable, and front-loads
the highest-value, lowest-risk win (build split + UI tests) before any framework
adoption. A rewrite, if ever justified, becomes low-risk *after* step 2 because
behavior is then test-pinned.

## 4. Framework decision (operator input wanted)

The container serves one static dashboard file today. Adding a build toolchain
is the real cost, independent of framework choice. Candidates:

- **React** — largest ecosystem/familiarity; heavier; needs bundler.
- **Preact / Svelte / lit** — far lighter, smaller bundle, simpler build; better
  fit for a single-served-file model; less ubiquitous.

Recommendation: pick the lightest option the team is comfortable maintaining;
the win is componentization + tests + a build, not React specifically. **Decision
point for operator: React vs a lighter framework.** (Defer until step 1/2 — the
extraction is framework-agnostic.)

## 5. Sequencing without regression thrash

Apply TEAMWORK_STRATEGY.md: each migration step gets a validation contract
(behavioral assertions) written first; an independent agent verifies behavior
end-to-end; deploy behind the AST+tests+clean-tree gate; serial execution on
shared files. Migrate lowest-risk pages first; keep the old path until the new
page passes its contract. API/MCP frozen the entire time.

Decision points the operator owns: (a) approve Option B; (b) framework choice;
(c) acceptable per-step verification bar; (d) whether bulk-actions wait for the
componentized base (recommended) or get a stopgap on the monolith.

## 6. Bridge / runtime / MCP (owned by comms-senior-dev-pi)

> _Pending comms-senior-dev-pi's independent critique + this section. Working
> position: FREEZE MCP and the HTTP API — they are the stable, tested contract;
> rebuilding them is high-risk with no demonstrated need. Bridge/runtime
> (`mcp/stdio`) is already modular and test-covered (28 files) and does not
> warrant a rewrite; targeted hardening only. pi to confirm/challenge and fill
> this section._

## 7. Explicit non-goals

- No big-bang rewrite. No MCP/API rewrite. No rewrite work on this branch.
- No framework lock-in decision before the framework-agnostic extraction (step 1).
