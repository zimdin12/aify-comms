# 0.9 agent state: what exists, what is missing before an end-to-end test (2026-10-04)

Measured 2026-10-04 16:50-17:20Z against env `next/0.9-agent-state` 8c15f29, comms `next/0.9-agent-state`
6bc9da68, wrapper `next/0.9-agent-state` 686d182. No code was written for this document.

Labels: **PROVEN** = read or run here against the real object; **PASSES IN TESTS** = green in a suite, never run
for real; **ASSUMED** = inferred, not checked. Review verdicts are quoted from comms-senior-dev's messages, by id.

**The end-to-end test this list aims at** (the first thing Steven can run): with 0.9 installed and one resident
claude and one managed hermes relaunched, aify-env shows each agent's derived state word (C3) from its own hooks
and processes, and aify-comms records its current answer beside aify-env's for each decision (C8, shadow only, no
switch). Everything after that (P6 classification, P7 switch, P8 deletions, P9 lifecycle) is out of this list.

## (a) The P2 pieces that exist

All of these are pure or IO-injected helpers. **None is called by the running aify-env**: PROVEN by grep of
`lib/` and `bin/` for each module's import (only each other import them; control: `process-registry.mjs` is found
imported by `lib/runner.mjs` and `lib/pane-buffer.mjs` with the same grep). That is the gap the rest of this
document is about.

| piece | where (env, file:line) | sha | verdict (quoted) |
|---|---|---|---|
| state word (C3 table) and the turn law | `lib/agent-state.mjs:52` `deriveAgentState`, `:22` `turnIsStillLive` (94 lines) | ad9900f, repaired 4295436 / e57d770 | "R1 and crossed row 2 CLOSED ... Other tested pure repairs ... accepted within their helper scope, not as runtime publication or whole P2 completion" (1790968865382-020ee2aa) |
| turn admission and ordering (C3) | `lib/turn-events.mjs:78` `applyTurnEvent`, `:96` `turnIsBusy`, `:123` `restoreTurns` (134 lines) | 4295436 | as above (020ee2aa): "R1 now refuses the unbound end before ordering ... CLOSED" |
| resident lifetime records (C4) | `lib/resident-lifetimes.mjs:24` parse, `:50` `verifyLifetime`, `:86` `windowsArguments`, `:133` `currentLifetimes` (163 lines) | 9a36ca6, repaired b5c6d61 | "APPROVE_SOURCE for the repaired pure lifetime slice at env b5c6d61" (1790971998322-1d5c0cf0) |
| publisher ordering (C5) | `lib/agent-state-publisher.mjs:16` `nextGeneration`, `:65` `class AgentStatePublisher` (123 lines) | bb9431a | "the publisher's acknowledged-view/last-event/lost-body controls are accepted within their helper scope" (020ee2aa) |
| durable generation file (C5) | `lib/publication-generation.mjs:29` `advanceGeneration`; `lib/durable-file.mjs:52` `writeFileDurably` | a80dbc7, repaired c1c67a3, 2b8e2ec, 8c15f29 | "CLOSE G2 and G3 mechanisms at 2b8e2ec ... APPROVE_IMPLEMENTATION" (1790977752416-a5af28de); comment "CLOSE M1/M2 / APPROVE_METADATA 8c15f29" (1790978987526-7bac90ed) |
| one OS probe per sweep (C4) | `lib/process-probe.mjs:29` `probeProcesses` (60 lines) | 0c18f91, repaired c1c67a3 | "The repaired probe projection is APPROVE_SOURCE" (1790975538813-4b38cc5e) |
| durable turns file (C3) | `lib/turns-file.mjs:35` `readTurns`, `:54` `writeTurns` | bb00c62 | "APPROVE_SOURCE the bounded turns/restore/path delta at bb00c62 ... assembly/dependencies HOLD" (1790976402061-be7d5b50) |
| per-instance descriptor (C4 routing) | `lib/instance-descriptor.mjs:29,38,52` (58 lines) | 0efd3e6, repaired d3af982 | "ACCEPT_REVISED_DESIGN the writer-only per-instance descriptor and CLOSE D1 at exact d3af982" (1790977752764-4bd0c8f5) |
| plugin interface (C10) | `lib/plugins/ports.mjs` (typedefs only, `export {}` at :53); `tests/every-plugin-meets-the-interface.test.js` | 514c1ee | no acceptance: "The ports/plugin conformance paths likewise have no reviewer acceptance" (1790959345478-477aac91); "remaining C10/plugin shape work are not closed here" (020ee2aa) |
| grants: unreadable definitions, config shape | env 0.9 13e127e (counterpart of release 83b9881) | 13e127e | "APPROVE_SOURCE ... and its 0.9 counterpart 13e127e" (a5af28de) |

Every acceptance above is bounded by the same sentence, repeated in each verdict: "whole P2/first-adoption assembly
still HOLD" (a5af28de). The one named assembly gate, from 1d5c0cf0: "First adoption with no stored turn must remain
unknown until its next hook; that assembly gate remains." PASSES IN TESTS (020ee2aa reproduced the opposite on an
unwired composition: "unwired adoption -> turnIsBusy(undefined) -> recognized derivation construction yields idle").

Nothing for P3 or P4 exists:
- wrapper 0.9 (686d182): no launcher writes a lifetime record or exports `AIFY_LIFETIME` / `AIFY_ENV_INSTANCE`.
  PROVEN by grep of `wrappers/` and `lib/` (no match).
- env 0.9: nothing injects `AIFY_LIFETIME` into a managed spawn. PROVEN by grep of `lib/`, `bin/` (no match).
- comms 0.9: no receiver for agent-state publications; the string appears only in three test files. PROVEN by grep
  of `service/` (control: `turn-start` is found in `api_core/claim_gating.py` and `hook_event_order.py`).
- aify-env's API has no route a hook can report to: `lib/protocol.mjs:42` `ROUTES` lists /health, /processes/*,
  /agents/startable, /agents/importable, /agents/:id/start, /agents/:id/herdr-space only. PROVEN (read).

## (b) What remains before the end-to-end test

Sizes are ASSUMED estimates (production lines + test lines), not measured.

| # | piece | repo, files | size | the test that proves it | depends on |
|---|---|---|---|---|---|
| G0 | bring 0.8.5 into the 0.9 branches (merge commit, never rebase) | env: conflicts in `lib/agent-definitions.mjs`, `lib/serving-endpoint.mjs`; comms: conflicts in VERSION, `.claude-plugin/plugin.json`, KNOWN_ISSUES, `mcp/stdio/package*.json`, `version.js`; wrapper: no conflict (10 commits) | resolution only | full suites of all three repos on the merged heads | 0.8.5 approved (it is in review, stalled since 10:17Z) |
| G1 | **the state host**: one module that composes the existing helpers into `current()` per agent | env: new `lib/agent-state-host.mjs` | ~200 + ~300 | temp home with resident records and a fake probe: verified / unknown / reused-pid lifetimes give C3's words; a restart restores turns by verdict (C3 table); **first adoption with no stored turn reads `unknown`, not `idle`** (the held gate) | nothing new; uses only the accepted helpers |
| G2 | wire the host into the daemon | env: `bin/aify-env.mjs` (997 lines, gate 1000: 3 lines of room), or C10's ports migration first to make room | 3 lines, or ~150 moved | the existing boot tests stay green; a boot in a temp home writes the descriptor and the generation | G1; see (c) R2 |
| G3 | the hook route | env: `lib/protocol.mjs` ROUTES + a handler module | ~120 + ~200 | each C3 admission row through the real route: unbound, conflict, not-current, identity-unknown end vs start, out-of-order, tie; the turn persists across a simulated restart | G1, G2 |
| G4 | a lifetime for every managed spawn | env: the spawn path's environment (`launchEnv`) + ProcessRegistry exit | ~60 + ~150 | a spawned worker's env carries `AIFY_ENV_URL`, `AIFY_ENV_INSTANCE`, `AIFY_LIFETIME`; its lifetime is `yes` from spawn and ends at exit; its first hook is admitted | G1 |
| G5 | P3: launchers and hooks | wrapper: resident lifetime record (`/proc/$$/winpid`, `$EPOCHREALTIME`), the two exports; comms: `install.sh` hook commands post to aify-env too, and the codex/hermes bridge allowlists forward both | ~150 across both + tests | per harness, resident and managed: a hook fired in a temp home reaches a test aify-env and is admitted | G3, G4 |
| G6 | a read surface Steven can look at | env: `aify-env agents` gains a state column, or a `GET /agents/state` route | ~60 + ~80 | the CLI or route shows the host's `current()`; unknown shows its cause | G1, G2 |
| G7 | the publisher's sender | env: a loop over services that opt in with `agentState` in `~/.aify/services.json` | ~150 + ~250 | fake fetch: only opted-in entries receive; snapshot every 60 s; `unavailable` on a partial enumeration; one push in flight; non-2xx dropped | G1, G2 |
| G8 | P4: comms ingest and the shadow comparison | comms: receiver route (C5 table, own transaction), stored records, the C8 recorder computing today's answer and `commsView` on the same evaluation, a doctor row | ~400 + ~500 | C5 receiver table cases; for each C8 decision, both answers recorded with inputs; nothing reads the new answer | G7, G0 |

managed codex turn events: from the app-server's own turn notifications, later slice
G6's read contract supplies `stoppedByOperator:false` with visible `inputs: { operatorStop: "not-tracked" }`; D9 adds the real producer and removes that marker.

G8a adds receiver/storage source only. It accepts the ordinary comms key in `x-aify-agent-state-key` on `POST /api/v1/agent-state` only, validates exact wire bytes, and keeps unavailable ordering separate from data application time. G8b's same-evaluation comparisons and doctor remain separate; neither C8 nor the switch is closed. The bounded G6 publication still supplies no `turn.ageMs` or running-code provenance for all three repositories. Those missing producers must be reported as unavailable, not synthesized into stale anchors or shadow agreements. Source publication is not installation or isolated end-to-end evidence.

G8b-1 instruments only partial freshness/base-word status comparisons; the other nine C8 owners remain uninstrumented post-0.9 work, and there is no switch in 0.9.

The **first point Steven can test** is after G0-G6: aify-env shows a live derived state per agent. The comms
side-by-side needs G7-G8 as well. herdr forwarding (D10), the docs (P2 item) and the C2 ledger's unmapped rows are
not needed for that test.

## (c) Blocked on a ruling

| # | ruling | owed by | blocks |
|---|---|---|---|
| R1 | 0.8.5 source verdicts (deleg_d55ebdd9; no output since 10:17Z) | comms-senior-dev | G0, and so the installable 0.9 |
| R2 | may `bin/aify-env.mjs` take the 3-line wiring, or must C10's ports migration land first? Memory records the ports migration as an assembly gate, but no ruling request was found in the inbox (searched "migration", "hooks route": none). So it is **not yet asked** | comms-senior-dev, once asked | G2 |
| R3 | the loopback hook route takes no key, like every existing aify-env route; a same-user process can post a turn event naming a lifetime. C4/D3 accept the route; the plan does not say who may post. ASSUMED acceptable under the existing posture, to be stated in G3's review request rather than decided silently | comms-senior-dev (G3 review) | G3 |
| R4 | P-1 (verified renewal) | Steven | not the test: `busy` follows `strict` until he chooses; it blocks the switch (P6/P7) |
| R5 | D8, D9, D12 (defaults he did not object to) | Steven | not the test; P9 |

Practical blocker beside the rulings: every piece goes through one reviewer, whose queue has been stalled for six
hours today.

## (d) Proposed order, and the first code piece

1. **G1, the state host**, first. It needs no ruling and no 0.8.5 code, touches no file 0.8.5 changed, composes only
   accepted helpers, and carries the one assembly gate the reviewer named (first adoption reads `unknown`).
2. Ask R2 alongside G1's review; G2 follows its answer.
3. G0 as soon as 0.8.5 lands, before G3: G3 and G4 touch the daemon's spawn and route code, which 0.8.5 also changed (ASSUMED from the
   0.8.5 file list; not diffed hunk by hunk).
4. G3, G4, G6 (Steven can test from here), then G5 so real hooks arrive, then G7, G8.

**The single first code piece:** `lib/agent-state-host.mjs` (G1), with its test in a temp home, red first on the
first-adoption case.
