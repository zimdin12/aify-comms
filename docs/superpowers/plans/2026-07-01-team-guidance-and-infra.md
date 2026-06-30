# aify-comms Team-Guidance + Infra Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Turn the multi-round teams-benchmark findings (A–G) plus live-DB conversation analysis (H + stranding-at-scale) into concrete guidance + infra fixes that make aify-comms teams build better software with less wasted process/tokens.

**Architecture:** Guidance lands as ONE new reference (`references/building-software.md`) + targeted edits to `references/teamwork.md`, wired as a first-read on register/managed-spawn — NOT a new or split skill (preserves the two-skill model and the byte-identical `.claude`/`.agents` mirror rule). Infra is two investigations into real reliability bugs the benchmark + DB surfaced (auto-start-on-send; stranded reply-capture). The 503 write-contention half is already fixed.

**Tech Stack:** Markdown skill references (mirrored in `.claude/skills/aify-comms/` and `.agents/skills/aify-comms/`); Python FastAPI service (`service/`) for the infra tasks; Node bridges (`mcp/stdio/`).

## Global Constraints

- Skill files live in TWO places and must stay **byte-identical**: `.claude/skills/aify-comms/...` and `.agents/skills/aify-comms/...`. Every guidance task edits both.
- Keep the two-skill model: only `aify-comms` (use) + `aify-comms-debug` (fix). Guidance is a REFERENCE under the existing skill, never a new skill.
- Guidance must stay GENERAL, short, and language-agnostic (principles a senior engineer holds), and explicitly scale rigor to task complexity — ponytail/YAGNI applies to the guidance itself.
- Service changes are COPY'd into the image: `docker compose up -d --build service` + `curl localhost:8800/health`. Python parse-check before rebuild.
- Bridge changes (`mcp/stdio/`) need `node --check` + `install.sh` re-run + wrapper restart.
- Already DONE (do not re-do): **E (503)** — claim path returns empty 200 (`6eb3263`); write path retries lock contention before 503 (`d069f51`).

## Evidence base (live DB, last 7 days, 1655 message bodies / 8409 dispatch runs)

| Signal | Count | Maps to |
|---|---|---|
| Stranded require-reply runs (done, no `result_message_id`) | **50** | E-stranding / new infra task T5 |
| "respawn / won't-wake / stayed-dormant / no live sidecar" | **13** | bug D (T4) |
| Channel vs direct messages (7d) | **31 vs 1624 (~2%)** | new finding H (T2) |
| Messages > 4000 chars | **37** | F context bloat (guidance) |
| "stuck / stalled / no-reply / went quiet" | **51** | G monitoring-tools (T2) |
| "reminder / overdue" | **54** | G + work-loop friction |
| Vague "on it" acks | **26** | worker discipline (already in guide; not followed → first-read enforcement) |
| APPROVE/REVISE/REWORK verdict tokens | **194** | review-verdict discipline IS working (keep) |

---

### Task 1: Author `references/building-software.md` (the general engineering standard)

**Files:**
- Create: `.claude/skills/aify-comms/references/building-software.md`
- Create (mirror, byte-identical): `.agents/skills/aify-comms/references/building-software.md`

**Interfaces:**
- Produces: a reference file SKILL.md (Task 3) and teamwork.md (Task 2) point to. Section anchors used by later tasks: `## Ownership`, `## Right-size the rigor`.

- [ ] **Step 1: Write the file** with exactly this content (adopts the reviewed draft + folds in F context-discipline and H channel-use; keeps the right-size-rigor rule prominent):

```markdown
# Building Software as an aify-comms Team

Read this once, at the start of any build/implementation task, before you split work or write code.
It is general on purpose: apply judgment, scale it to the task, and let language idioms win over dogma.

## Ownership — someone owns the WHOLE
- Every team has ONE **driver/owner** (usually the manager/lead). The driver owns the integrated
  product end-to-end and the SEAMS between teammates — the parts no single lane owns.
- Teammates own and prove their slice. The driver owns "does the assembled thing actually work, as a
  user, end to end." "Every slice was approved" is NOT "the product works." If it ships broken, that is
  the driver's miss.
- Before declaring done, the driver personally exercises the whole experience (the real flow) and the
  cross-cutting concerns: data flowing across layers, consistent UX/controls, auth→action→persistence,
  restart/recovery.

## Architecture & code quality — build it like it's going to production
- Write idiomatic, well-architected code for the language at hand. Use strong OOP/SOLID where the
  language and problem call for it; clean functional/modular composition where they don't. Do not
  cargo-cult OOP into code that doesn't want it — "production-grade" means appropriate, not ornate.
- Clear module boundaries; separate concerns (transport, domain, data, UI); name precisely; keep
  functions focused.
- Defensive by default: validate and bound ALL external input (including upper bounds — assume clients
  lie/cheat), parameterize every query, never trust the client, handle the unhappy paths (network down,
  stale/expired auth, empty state, restart). Fail with clear, actionable errors.
- DRY the SHARED CONTRACTS: agree the data model / API shapes / interfaces ONCE, up front, and keep the
  implementation and the written contract in lockstep — a doc that drifts from the code is a bug.
- Cover ALL areas the task implies, not just the happy path: security, validation, error handling, edge
  cases, persistence/migration, accessibility, performance where it matters.

## Testing — prove behavior, and make it testable
- Write automated tests for real behavior (not assertions that it "should" work). For a service, drive
  the real thing (boot it over HTTP against a throwaway DB). Architect FOR testability (e.g. an app
  factory separate from `listen`).
- Tests are part of "done," not optional. A reviewer's APPROVE should be backed by tests passing.

## Reviewing — reviewer ≠ builder, and verify behavior not just text
- Every non-trivial piece is reviewed by someone who didn't build it. Reviews END with an explicit
  `APPROVE` or `REVISE` (revise = the specific, checkable changes).
- Distinguish CODE REVIEW (read the diff on disk) from BEHAVIORAL VERIFICATION (run it / measure it).
  Anything user-facing, render-, feel-, or integration-affecting MUST be behaviorally verified — code
  review alone does not catch these. Say which you did.

## Discussion — agree the seams before you build them, on the channel
- For interdependent work, discuss and FREEZE the shared contracts (data model, API, auth, the module
  interface between lanes) on a CHANNEL before lanes go heads-down. A team channel keeps shared
  decisions in one place instead of fragmenting them across DMs — use `comms_channel_send` for
  decisions everyone needs; use DMs for owned 1:1 handoffs. A runnable skeleton against the frozen
  contract beats a long spec.

## Context discipline — keep sessions lean
- Hand down only the inputs a subtask needs (the file, the one prior result, the exact decision), not
  the whole thread. For long or binary content use `comms_share` + a one-line pointer instead of pasting.
- On a long-running session, compact periodically (`comms_compact` handoff, or the runtime's own
  `/compact`) so accumulated context doesn't silently inflate every turn's cost.

## Honesty — proven vs assumed
- Never overclaim. State plainly what is PROVEN (you ran/measured it), what is ASSUMED (reasoned but not
  exercised), and what you could not verify. Honest gaps build trust; hidden ones destroy it.

## Right-size the rigor — match effort to complexity & risk
- Scale the process to the task. A novel/complex/risky build earns the full gauntlet (multiple
  reviewers, deep verification, more discussion). A small, standard, low-risk change does NOT —
  over-coordinating it wastes tokens and time for no quality gain. More agents and more review rounds
  are a COST, not a virtue — spend them where they buy something. Read the task; pick the lightest
  process that still protects correctness and the user.
```

- [ ] **Step 2: Mirror byte-identical** to `.agents/skills/aify-comms/references/building-software.md`.

- [ ] **Step 3: Verify the mirror matches** — `diff .claude/skills/aify-comms/references/building-software.md .agents/skills/aify-comms/references/building-software.md` → no output.

- [ ] **Step 4: Commit** — `git add .claude/skills .agents/skills && git commit -m "docs(skill): add building-software team engineering standard"`.

---

### Task 2: Edit `references/teamwork.md` (driver role, integration gate, review split, testing, right-size, console-first, channels)

**Files:**
- Modify: `.claude/skills/aify-comms/references/teamwork.md`
- Modify (mirror): `.agents/skills/aify-comms/references/teamwork.md`

**Interfaces:**
- Consumes: `building-software.md` (Task 1) — link to it, don't duplicate its content.

- [ ] **Step 1 — Roles (after the `operator` line, L11):** add
  `- `driver`/owner: owns the integrated product end-to-end and the seams between lanes; personally exercises the whole experience before "done." Distinct from per-lane ownership — dashboard-status and integration-order are not the same as owning that the product works. (See `references/building-software.md`.)`

- [ ] **Step 2 — Autonomous Loop (after step 4, L35):** add a final step
  `5. Before "done," the driver INTEGRATES and behaviorally verifies the WHOLE — end-to-end flow + the cross-cutting concerns no single lane owns (controls/UX consistency, data across layers, auth→action→persistence, restart/recovery) — not just each approved slice.`
  (and renumber the old step 5 "Manager reports…" to 6).

- [ ] **Step 3 — Review Discipline (replace L77 "Verify on disk before approval"):**
  `- Distinguish CODE REVIEW (read the diff on disk) from BEHAVIORAL VERIFICATION (run it / measure it). For any user-facing, render-, feel-, or integration-affecting change, behavioral verification is REQUIRED — code review alone misses render/feel/integration bugs. State which you did.`

- [ ] **Step 4 — add a Testing bullet under Worker Discipline:**
  `- Automated tests for real behavior are part of "done," not optional; architect for testability (e.g. an app-factory seam). An APPROVE should be backed by tests passing.`

- [ ] **Step 5 — add two bullets to the TOP of Manager Discipline (these are the highest-DB-frequency stalls):**
  `- **Stuck? Peek first.** When an agent looks stalled/overdue, `comms_console_tail(agentId=...)` (managed) or a focused `[STATUS]` probe (resident) BEFORE re-spawning or reminding — reach for the console as the reflex, not the filesystem.`
  `- **Right-size the rigor.** Scale review depth + teammate count to task complexity and risk; don't run the full multi-reviewer gauntlet on trivial/low-risk work. More agents/reviews are a COST, not a virtue. (See `references/building-software.md`.)`

- [ ] **Step 6 — add a channels nudge under the existing context-scoping bullet (L58):**
  `Put shared DECISIONS (frozen contracts, API shapes, integration order) on a team CHANNEL via `comms_channel_send`, not scattered across DMs — DB shows teams default to ~98% DMs, which fragments shared context. DMs are for owned 1:1 handoffs.`

- [ ] **Step 7: Mirror** all edits byte-identical to `.agents/...`, `diff` to confirm, **commit** `docs(skill): teamwork — driver role, integration gate, review-vs-verification, right-size, channels`.

---

### Task 3: Wire `building-software.md` as the first-read on register / managed-spawn

**Files:**
- Modify: `.claude/skills/aify-comms/SKILL.md` (+ `.agents` mirror) — top-of-file pointer
- Modify: the managed-spawn initial-prompt seed (find in `service/` spawn path or `mcp/stdio/` — Task step 2 locates it)

- [ ] **Step 1 — SKILL.md pointer:** directly under the `# aify-comms` H1 (after L6), add
  `> **Building something as part of a team?** Read `references/building-software.md` first — it is the team engineering standard (ownership, architecture, testing, review, and right-sizing effort to the task).`
  Mirror to `.agents`, `diff`, no other change.

- [ ] **Step 2 — locate the managed-spawn prompt seed:** `grep -rnE "initial_message|initialMessage|seed|standing instructions|first.?run" service/ mcp/stdio/ | grep -iE "spawn|prompt|initial"`. The spawn flow that seeds an agent's first delivered message / standing instructions is the hook.

- [ ] **Step 3 — seed one line** into that initial-prompt/standing-instructions string (NOT a paste — a pointer):
  `"Before you build, read the aify-comms team engineering standard (skill reference references/building-software.md) and apply it; right-size your process to the task."`
  Keep it behind whatever existing seed text there is; do not duplicate if a seed already references it.

- [ ] **Step 4 — verify** the seed renders in a spawn: rebuild service if it was a `service/` change (`docker compose up -d --build service`), then `comms_spawn` a throwaway agent and confirm the initial message contains the pointer (read via `comms_inbox` or dashboard). Tear down the test agent (`comms_remove_agent`).

- [ ] **Step 5: Commit** `feat(spawn): seed building-software first-read into managed kickoff + SKILL pointer` (+ mirror). If the seed touched `mcp/stdio/`, note in the commit that `install.sh` re-run + wrapper restart is required to deploy.

---

### Task 4: Investigate + fix bug D — `available` managed agent does not auto-start on send after a bridge restart

**Files:**
- Investigate: `service/routers/api_v2.py` (the `/messages/send` dispatch path + spawn-claim), `service/main.py` (reconcile), `mcp/stdio/` claim loop.
- Test: `service/tests/` (new pytest for the cold-start-on-send contract).

**Symptom (benchmark + 13 DB mentions):** sending to an `available` managed agent after the bridge returned did NOT cold-start it — `messages/send` returned `dispatchRuns: []`, the agent stayed dormant ("no live sidecar heartbeat"); only a manual `comms_spawn` relaunched it. SKILL.md L114 promises auto-start.

- [ ] **Step 1: Reproduce** — register a managed agent on a live env bridge, let it go `available` (stop its worker but keep the spec), restart the bridge, then `comms_send` to it. Capture: does `/messages/send` create a dispatch run + spawn-claim, or return `dispatchRuns: []`? Record the agent's `env_reachable` / session state at that moment.

- [ ] **Step 2: Trace the decision** — in the `/messages/send` handler, find where it decides to enqueue a spawn-claim vs treat the target as non-deliverable. The likely fault line: after a bridge restart the agent's `agent_sessions`/env binding is stale, so the deliverability check classifies it as "no live wake path" and skips the spawn enqueue. Confirm which predicate gates it.

- [ ] **Step 3: Write the failing test** — a pytest that, with a managed agent in `available` (no live worker) but a reachable env bridge, asserts `/messages/send` enqueues a spawn-request (non-empty `dispatchRuns` or a queued spawn) rather than returning empty. Run it; expect FAIL.

- [ ] **Step 4: Fix** — make the send path enqueue a spawn-claim for an `available` managed target whose env is reachable, even when the prior session binding is stale (re-bind to the env rather than requiring a live sidecar). Keep the existing offline/stopped hard-block intact (do not auto-start a `stopped`/offline agent). Run the test; expect PASS; run the full suite `python -m pytest service/tests/ -q`.

- [ ] **Step 5:** If Step 2 proves the server already enqueues correctly and the gap is bridge-side (the restarted bridge not claiming the spawn), fix in `mcp/stdio/` instead and note the `install.sh`-rerun deploy requirement. Either way: rebuild/redeploy, re-run the Step 1 repro live, confirm the agent now cold-starts. **Commit** with the exact root cause in the message.

---

### Task 5: Investigate stranded reply-capture — 50 require-reply runs finished with no captured reply

**Files:**
- Investigate: `service/routers/api_v2.py` (dispatch run completion + `managed_reply_capture_fallback`), `service/main.py` reconcile.

**Symptom (DB):** 50 `dispatch_runs` with `require_reply=1`, `status IN (completed,failed,cancelled)`, and empty `result_message_id`. The benchmark saw at least one dispatch strand entirely (PO never woken). Either the agent ended its turn without the `comms_send` reply AND the fallback mirror didn't fire, or the reply landed but wasn't linked back to the run.

- [ ] **Step 1: Characterize the 50** — `SELECT runtime, status, COUNT(*) ... GROUP BY runtime, status` for those stranded runs. Is it concentrated in one runtime (e.g. resident claude / a specific managed type) or spread? Pull 3 examples and check whether a reply message with matching `in_reply_to` actually exists (reply landed but link failed) vs no reply at all (turn ended silent).

- [ ] **Step 2: Branch on the finding:**
  - If replies EXIST but aren't linked → fix the run-close linkage (match the reply's `in_reply_to` to the run's `message_id` and set `result_message_id`); add a reconcile sweep that closes such runs. Write a pytest that a reply with matching `in_reply_to` closes its require-reply run.
  - If turns end SILENT and `managed_reply_capture_fallback` is enabled → confirm the fallback actually mirrors on those runtimes; if disabled/failing, that is the gap (fix or document). If it's resident sessions ending without the `comms_send`, that's a guidance miss already covered by SKILL.md "do not end a turn silently" — quantify and note it; no code fix.

- [ ] **Step 3: Implement the fix indicated by Step 2** (linkage repair OR fallback fix), with a pytest. Run full suite; rebuild service; re-query the stranded count to confirm it stops growing. **Commit** with the root cause. If the verdict is "guidance, not code," record it in the plan's findings instead and skip the code change (YAGNI).

---

### Task 6 (note, not work): E — 503 write contention — ALREADY FIXED

No action. Recorded for completeness: claim path returns empty 200 under contention (`6eb3263`); the central route handler retries write-lock contention before surfacing 503 (`d069f51`). Hosts running their own service must `git pull && docker compose up -d --build`. The Task 5 stranding investigation will confirm whether any residual stranding survives the 503 fix.

---

## Self-review

- **Spec coverage:** A→T2 (driver role) + T1 (ownership); B→T2 (integration gate); C→T1+T2 (review vs verification); D→T4; E→T6 (done); F→T1 (context discipline) + T2 (right-size); G→T2 (console-first + reminders); H (new, channels)→T1+T2; stranding (new)→T5. All findings have a task.
- **No placeholders:** T1 ships full file content; T2/T3 give exact insert text + anchors; T4/T5 give concrete repro + branch logic (the one unknown — D's server-vs-bridge fault line, and T5's replies-exist-vs-silent branch — are framed as investigations with both branches specified, not hand-waves).
- **Lean (ponytail):** no new skill, no new table, no speculative features; T5 explicitly allows a "guidance not code" YAGNI exit; guidance itself carries the right-size rule.
