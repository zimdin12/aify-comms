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
