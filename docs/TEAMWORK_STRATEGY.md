# aify-comms — Multi-Agent Teamwork Strategy

> **HISTORICAL TEAM RETROSPECTIVE.** Current agent teamwork guidance lives in
> `.agents/skills/aify-comms/references/teamwork.md` (mirrored under `.claude/skills/`).
> Old status examples below predate the proof-based six-state engine.

Operating rules for how this team (operator + `comms-tech-lead` + `comms-senior-dev-pi`
+ spawned managed agents) runs multi-day, multi-agent work without drift.

Distilled from Factory's "Missions" talk and **mapped to friction we actually hit
on `feature/dashboard-console-mode`** — so these are corrections, not theory.

## The five primitives we compose

| Primitive | What it is | How we use it |
|-----------|------------|---------------|
| Delegation | One agent scopes work to another and gets a result | Operator → tech-lead; tech-lead ↔ senior-dev-pi lane splits; spawning workers |
| Creator / Verifier | Builder and checker are **separate agents with separate context** | tech-lead implements service/dashboard, senior-dev-pi independently verifies (and vice-versa). Never self-certify a risky change. |
| Direct communication | Agents DM without a coordinator | Allowed for tight lane coordination; risky — state fragments. Keep a single source of truth (git + the consolidated report). |
| Negotiation | Agents align over a shared resource | Lane boundaries (service vs bridge), schema/contract changes — agree the contract *before* coding both sides. |
| Broadcast | One → many: shared constraints/status | The single consolidated done-report; this doc; DECISIONS.md. |

## Three roles

- **Orchestrator** (tech-lead): scopes with the operator, splits lanes, writes the
  validation contract, reviews, reports. Does not also rubber-stamp its own work.
- **Worker**: implements one scoped item with clean context, then **commits and
  sends a one-line status** so the next step inherits a clean slate.
- **Validator**: a *fresh* agent that did not write the code. Runs tests/lints **and
  verifies behavior end-to-end**. Adversarial by design.

> Our codex/pi-console and turn-busy work went smoothly **because** creator
> (tech-lead) and verifier (senior-dev-pi) were separate. The status
> whack-a-mole went badly because the same agent kept changing and judging
> "working" with no independent check and no written definition of correct.

## Validation contracts — write "done" before code

The single biggest lesson from our own failures.

- Before changing behavior, write the **correctness assertions** for it, independent
  of implementation, in the task/PR/DECISIONS.md.
- The agent status saga (idle-shows-working ↔ working-shows-active, reverted twice)
  happened because there was **no written status contract**. The contract should
  have been stated first, e.g.:
  - `working` ⇔ a genuinely active run/turn (claimed/running dispatch run **or**
    bridge `turnBusy`), never console byte-activity, never a queued/lingering
    delivered run.
  - attached-but-quiet console ⇒ `active`, not `working`.
  - stale heartbeat ⇒ `idle`/`offline` by the freshness windows.
- Tests assert the contract, not the code that was written. Tests written *after*
  implementation confirm decisions; they don't catch drift.

## Serial execution + targeted parallelism

- Default to **serial** for anything that writes shared state (code, the same files,
  the deployed container). Parallel writers conflict and the coordination overhead
  eats the speed — and worse: the **rebuild-mid-edit outage** was exactly an unsafe
  concurrent edit + deploy with no gate.
- Parallelize only **read-only / independent** work: research, audits, reviewing
  different files, the tech-lead/pi two-lane split where lanes don't touch the same
  files (service vs mcp/stdio).
- Hard rule that came out of the outage: **never `docker compose up -d --build`
  while any `service/` file is uncommitted/mid-edit**; gate every deploy on
  AST-check + `python -m unittest service.tests.test_api_v2_regressions` green +
  clean tree. This is our deploy "validation contract".

## Structured handoffs (self-healing)

Every worker handoff states, in the commit message and the comms status line:
- what was completed, what was intentionally left, what's blocked;
- the verification run and its result (e.g. "142/142", "28 stdio files green");
- issues discovered + whether a reply/wake is owed.

A bare "done" is not a handoff. Checkpoint-commit per item with a one-line
status — the discipline `comms-senior-dev-pi` adopted after two silent-gap
lapses; codify it for everyone. Silent multi-hour work with no checkpoint is a
process failure even if the code is fine.

## Right model in the right seat

- Planning / coordination / adversarial review → strongest reasoning model
  (tech-lead, Opus-class).
- Implementation → fast, fluent coding model (senior-dev-pi / spawned workers).
- Validation benefits from precise instruction-following; ideally a *different*
  context (and where it matters, a different model family) so it isn't biased by
  the implementer's assumptions.
- The operator owns model/effort policy globally (dashboard Settings → Runtime;
  managed pi gets `--thinking <effort>`); per-role tuning is deliberate, not ad hoc.

## Mission control = the dashboard

The dashboard is our mission-control: at a glance — who is actually working
(accurate turn-busy status), what's queued, run/handoff health, the `⌛`
waiting-for-input hint, DB/health. Keeping *that* honest is why status accuracy
is treated as a top-priority correctness issue, not cosmetic.

## Keep the architecture model-proof

Orchestration logic lives in prompts/skills/docs (this file, the aify-comms
skills, DECISIONS.md), not hard-coded state machines. Deterministic code stays
thin and does bookkeeping (validation gates, handoff enforcement, dispatch
lifecycle). Models supply intelligence; the system supplies discipline.

## Concrete standing rules for this team

1. Risky/behavioral change → write the correctness assertions first (contract),
   then implement, then an independent agent verifies behavior, then deploy
   behind the AST+tests+clean-tree gate.
2. Service is tech-lead's lane; `mcp/stdio` bridge/runtime is senior-dev-pi's.
   Cross-lane changes are negotiated (contract agreed both sides) before coding.
3. Checkpoint-commit + one-line comms status per item. No silent multi-hour work.
4. Bridge-side changes are **dormant until the host bridge restarts** — every
   report involving them must say so explicitly.
5. One consolidated status to the operator at the end of a batch; no per-item
   spam, but never go silent on a blocker.
6. Don't big-bang rewrite a working production system; stage it behind stable
   contracts (API/MCP frozen) with verification at each step.
