# 0.9 delta: configured model versus the model a lifetime actually runs (proposed, 2026-10-04)

A proposed addition to `2026-10-02-aify-env-owns-agent-state.md` (approved at 51da6a38). It is NOT covered by
that plan's P2 GO; comms-senior-dev asked for this measured delta first.

## The ask

Steven, 2026-10-04: the model is saved per agent; it can be changed in config (aify-env, perhaps aify-comms);
we can see what actually runs and switch on a mismatch; a new model must not mean visiting every agent. His
current workflow is to stay as it is.

## What was measured (read-only, this host, 2026-10-04 04:00-04:30Z)

Labels: OBSERVED = read from the real file or row; SOURCE = read in code; ASSUMED = inferred.

| # | Fact | Label |
|---|---|---|
| M1 | 23 of 24 definitions (`~/.aify/agent-definitions/*.json`, `agent.model`) are `""`; sc-critic is `"sonnet"`. aify-comms' agent rows match. | OBSERVED |
| M2 | Harness defaults: `~/.claude/settings.json` `model: "opus"`, `effortLevel: "high"`; hermes `AppData/Local/hermes/config.yaml` `model.default: gpt-6.1-sol`, `reasoning_effort: high`. These are the CURRENT desired defaults, not what any running lifetime started with. | OBSERVED |
| M3 | Claude: each assistant line of the session transcript (`~/.claude/projects/<cwd-slug>/<session-id>.jsonl`) carries `message.model`, the model that answered, with its `timestamp`. CORRECTED 2026-10-05: it also carries top-level `effort` and `perTurnEffort` (Claude Code 2.1.282 to 2.1.289, every assistant line in 27 recent transcripts). Every value seen is `high`, the settings default, so whether it reports a non-default effort is UNVERIFIED. | OBSERVED |
| M4 | Claude binds per lifetime: aify-comms' `sessionHandle` for each claude agent equals its transcript's file name. 13 agents mapped to 13 transcripts written in the last 24 h. | OBSERVED |
| M5 | Claude pins its model when a session starts. Three agents run `claude-opus-5` while the alias `opus` now resolves to `claude-opus-5-5` for new sessions: sc-lead (answering at 04:15 today), mc-manager, llama-manager. sc-critic (`sonnet`) runs `claude-sonnet-5-5`. | OBSERVED (the pinning cause is ASSUMED from the pattern) |
| M6 | Claude transcripts reach 1.6 GB (sand_castle 502989ba); one exceeded node's string limit when read whole. A producer reads the tail only. | OBSERVED |
| M7 | Hermes: `state.db` `session_model_usage` holds rows per (session, model, task) with `first_seen` and `last_seen`. `task = ''` is the agent's own turns; `compression`, `title_generation`, `background_review` are side tasks on other models. | OBSERVED |
| M8 | Hermes binds per lifetime: the agent's `sessionHandle` is the `state.db` session id. | OBSERVED |
| M9 | Hermes follows the configured default as it changes: comms-senior-dev's one session ran gpt-5.5, 5.6, 6-astra, 6-sol, then 6.1-sol. graph-senior-dev's latest main-task row is gpt-6-sol at 2026-10-02 17:15, and it has not run since. | OBSERVED |
| M10 | Hermes `sessions.model` is NOT the actual model: graph-senior-dev's reads gpt-6.1-sol while its latest usage is gpt-6-sol. | OBSERVED |
| M11 | Codex: rollout files (`~/.codex/sessions/YYYY/MM/DD/rollout-*-<thread>.jsonl`) carry `model` and `effort` per turn; the app-server `thread/start` answer carries `model` and `reasoningEffort` (sealed probe, evidence/2026-10-01-p6). No codex agent is defined today. | OBSERVED once; binding to `sessionHandle` ASSUMED |

## Three values, kept apart

- **desired**: the definition's `model`/`effort`; `""` means "the harness default", resolved for display from M2
  and labelled as the harness default, never written back into the definition.
- **resolved at start**: what the lifetime was started with. For a managed start this is the spawn spec
  (`as_its_spec_declares`); for a resident or a resumed session nothing records it today.
- **actual**: the latest observation tied to the exact lifetime (M3/M4, M7/M8, M11), with its timestamp.

Comparison happens only between desired (resolved) and actual. A missing, unsupported or stale actual is
UNKNOWN: never a mismatch, never a switch trigger. Stale = older than the lifetime's last turn end, or no main
turn since the desired value last changed.

Aliases: `opus`/`sonnet` (Claude) resolve to dated ids the harness chooses. The plan does not guess
equivalence from spelling. A desired alias is compared with the id that a NEW lifetime of the same harness
reports (the newest observation across that harness's lifetimes started after the desired value was set); with
none, the comparison is UNKNOWN.

## Producers (aify-env, per lifetime)

aify-env owns lifetimes in 0.9 (D1-D4) and publishes `runsWith` in the agent-state feed (D5). It reads:
- claude: the transcript tail (bounded, e.g. last 256 KB) for the newest `type: assistant` line's `message.model`
  and `timestamp`, located by `sessionHandle`;
- hermes: `state.db` opened read-only, newest `session_model_usage` row with `task = ''` for the handle;
- codex: the rollout file for the thread, newest turn's `model` and `effort`.

Read on turn end, not on a timer. A read failure is UNKNOWN with its cause.

## Operator gating, and what this does NOT do

- Reporting is one feature; switching is another, operator-selected. Detecting a mismatch changes nothing.
- No automatic definition rewrite, no automatic restart or resume, no in-session switch.
- Bulk set: `aify-env agents set --model X` takes an explicit selection (`--harness`, `--ids`, or `--all`) and
  prints a preview of each agent's before/after; it writes only with `--yes`. Agents with their own model (today
  sc-critic) are listed as exceptions and skipped unless named. Each write carries its expected revision, and a
  definition changed meanwhile is refused for that agent (SOURCE: `DefinitionStore` define takes an `expect` {incarnation, revision} compare-and-set, aify-env lib/agent-definitions.mjs:206). From aify-comms
  the same goes through operator-gated change requests.
- Switching a running lifetime is a separate operator action, "restart into its definition", per agent, with
  its cost stated per harness: claude resumes the same transcript and MAY keep its pinned model (M5, to measure
  before promising); hermes picks up the configured default at its next start (M9); codex starts a new thread
  unless resumed.

## Where the lifetime binding comes from (decision for review)

An observation is tied to a lifetime by the native session handle (M4, M8). aify-env does not hold one today:
definitions carry none, and `agents import`'s pull (`lib/plugins/aify-comms/agent-import-records.mjs`,
`importRecord`) keeps only definition fields, so it drops the handle aify-comms reports (SOURCE).

- **Proposed for the first testable slice:** a new read-only pull on the aify-comms plugin's agents capability
  that returns, for this machine's agents, `{id, harness, sessionHandle}` from the roster aify-comms already
  serves. `aify-env agents models` reads it, then the producers above. A handle aify-comms does not report is
  UNKNOWN ("no session handle").
- **Replaced later, not rebuilt:** when aify-env's own lifetime records (P2) carry the handle per lifetime, the
  command reads those instead; the producers and the comparison do not change.
- **Rejected:** scanning transcript or session directories by workspace. Several agents share a workspace
  (sand_castle's folder holds 11 claude transcripts, 6 written in the last 24 h), so a directory cannot name its agent.

## Open measurements before building

1. Claude: does `--resume` with `--model X` change the pinned model of a resumed session? (decides whether a
   switch can keep the conversation).
2. Claude: does the transcript's `effort` change when a lifetime runs at another effort? Present on every assistant line (M3), but only `high` has been seen; a lifetime started at another effort settles it.
3. Codex: confirm the rollout file to `sessionHandle` binding on a real managed codex lifetime.
