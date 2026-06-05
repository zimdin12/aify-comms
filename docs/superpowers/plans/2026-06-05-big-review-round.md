# Big Review Round — harness symmetry, flag/doc accuracy, skills, features (2026-06-05)

**Goal:** A thorough, read-only review of aify-comms across four axes, producing a findings report that feeds a SEPARATE improvements/fixes plan. Nothing is changed in this round — review only, trace everything against the actual code, and classify each asymmetry/mismatch as **intentional+justified** vs **accidental/bug**.

**Hard rule:** every claim must be traced to code (`file:line`), not assumed. "All should stay working" — so the fixes plan that follows must preserve current behavior; this review just finds the gaps.

**Method:** parallel read-only subagents, one per slice, each returns a structured findings list. Then synthesize a symmetry matrix + a prioritized fixes plan. No edits, no opencode execution.

---

## Axis 1 — Harness consistency & symmetry (the core ask)

Five runtimes: **claude-code, codex, hermes, pi, opencode**. For EACH, trace these dimensions and record the concrete mechanism + file:line; then a synthesis compares them and flags asymmetries.

Dimensions (the symmetry matrix columns):
1. **Register / identity** — how the agent registers; session-id source (captured / resume / native).
2. **Spawn (managed)** — how a managed worker is launched (wrapper, gateway, controller, RPC, PTY).
3. **Delivery shape** — channel-sidecar / gateway-WS / controller-inject / RPC; idle vs mid-run (steer).
4. **Turn-start signal** — hook / event / heartbeat / poll → which server endpoint (`/turn-start`, `/heartbeat` turnBusy, dispatch).
5. **Turn-end signal** — same.
6. **Status feed** — does `working` reach BOTH engines (turn_busy old + agent_status_state in_turn new)? known gaps?
7. **Session resume** — native id form; durable-vs-ephemeral; survives restart; explicit `--resume` path; bash↔PS1 parity (hermes).
8. **Interrupt / steer** — supported? mechanism.
9. **Kill / reap** — managed worker teardown, orphan reap, self-exit.
10. **Resident vs managed** — which modes supported (per DECISIONS); presence-only?
11. **Flags / env** — runtime-specific env vars + CLI flags.

Output per harness: the row + any **asymmetry flagged** (is claude's transcript-detector replicable for codex? does every managed runtime feed the new engine? are kill/reap paths uniform? etc.), each tagged intentional vs bug.

## Axis 2 — Flags & config vs the latest docs

Enumerate EVERY operator-facing knob and cross-check code ↔ docs:
- **Env vars** (`AIFY_*`, `HERMES_*`, `AIFY_NO_*`, `*_KEEPALIVE_*`, strict-mcp, yolo, etc.) — grep the code for `process.env.*` / `os.environ` / `getenv`; for each, confirm it's documented in README / install.*.md / DECISIONS and the doc matches the code's actual default/behavior.
- **CLI flags** the wrappers accept (`--resident`/`--managed`/`--resume`/`--aify-agent`/`--yolo`/...).
- **Settings** (the `settings` table keys: `status_engine`, idle/offline seconds, lease TTLs, backstops) — documented? defaults match?
- Flag the ones that are: undocumented, documented-but-stale, default-mismatch, or removed-but-still-referenced.

## Axis 3 — Skills review (research-backed)

- Read Anthropic's current skill-authoring guidance (web research + the `skill-authoring-best-practices` memory) — what makes a good SKILL.md (frontmatter description quality, progressive disclosure, length, when-to-use triggers, tool scoping, examples).
- Review both skills — `aify-comms` (usage) and `aify-comms-debug` (troubleshooting) — against that guidance: are they discoverable (description triggers), right-sized (the debug skill has grown large — does it need splitting / progressive disclosure?), accurate (no stale entries), and is the usage skill a clean quickstart vs reference?
- Verify `.claude` ↔ `.agents` mirror parity is maintainable (is duplication the right model, or a symlink/generation?).
- Output: concrete skill-optimization recommendations (don't edit yet).

## Axis 4 — Feature completeness & correctness

Enumerate the feature surface (the `comms_*` MCP tools + dashboard capabilities): messaging (send/inbox/read/unsend), channels, file sharing, dispatch/spawn, console (tail/input), status, sessions (resume/handle/delete), envs, search, contracts, describe. For each: confirm it's wired end-to-end (tool → server → effect) and surfaced in docs/skills; flag any tool that's broken, undocumented, or orphaned.

---

## Axis 5 — UI & UX (operator UI + AGENT UX)

Two audiences:
- **Operator UI/UX (dashboard):** is the dashboard consistent + legible — status dots/labels match the 8-status vocab everywhere; lifecycle verbs (spawn/stop/restart/reset/resume/steer/handle) are discoverable + named consistently; console attach/tail is clear; session/handle controls are coherent; empty/error/loading states; no stale labels. (Note: the NEW dashboard is the migration target — review the CURRENT one for parity gaps to carry over, don't redesign.)
- **AGENT UX:** how does an agent EXPERIENCE the system — are the `comms_*` tool descriptions clear + consistent; is the dispatch/reply contract obvious (inReplyTo, reply-in-same-turn for managed); are error messages actionable (a blocked send tells the agent why); is the channel-wake payload well-formed; do skills give an agent the right mental model fast? Symmetry here too: does every runtime's agent get the same affordances (steer, interrupt, resume, console)?

## Tagging (operator's rule)
Every finding tagged **CRITICAL** (breaks correctness / symmetry that bites / wrong docs that mislead) vs **OPTIONAL** (polish / nice-to-have / cosmetic). Only act on what we're SURE about; "don't break it too much." The fixes plan will sequence CRITICAL-first, OPTIONAL behind explicit go-ahead.

## Collaboration
**comms-senior-dev** (hermes, focus: runtime adapters + UI polish) runs his OWN parallel review plan on the SAME axes, weighted to symmetry + UI/UX. Merge his findings with this review's; discuss/reconcile disagreements over comms before finalizing the fixes plan. Two independent passes → higher-confidence findings.

## Execution
- Wave 1 (parallel): 5 harness-trace agents (Axis 1) + 1 flags/docs agent (Axis 2) + 1 skills agent (Axis 3) + 1 features agent (Axis 4) + 1 UI/UX agent (Axis 5). In parallel, comms-senior-dev runs his own pass.
- Synthesize: the symmetry matrix + a single findings report, each item tagged {symmetry|flag|skill|feature} × {bug|stale-doc|improvement|intentional} × severity.
- Deliverable: this review's findings → a NEW plan `2026-06-05-review-fixes-plan.md` (prioritized, safe, non-breaking), presented before any implementation.

**Pre-existing known state (don't re-flag as new):** 4 pre-existing bridge-test failures (hermes-register-fresh-handle, hermes-runtime, server-url-fallback, wrapper-backed-resident-claim); status_engine=new is live; opencode never executed on this host.
