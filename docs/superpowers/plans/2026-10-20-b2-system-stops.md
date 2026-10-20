# B2: the stops aify-env never hears about (2026-10-20, revision 2, design only)

Scope, from `2026-10-18-status-switch.md` (the 19:00Z entry): operator stops of DEFINED agents already go
through aify-env's journal (D9c, `lifecycle_delegation.py:53`). B2 covers every other write that makes comms
derive `stopped`/`disabled` (`status_inputs.py:239-242`: `status='stopped'` OR `launch_mode='none'`) with no
journal entry. Revision 2 answers review REVISE 37434413 (R1-R5). The sc-manager and mc-manager rows are out of
scope: ordinary registration replaced them live (sc-manager at 14:02:47Z), and console adoption is the one branch
that preserves a stop (`agent_registration_writes.py:109-124`).

## The population

Every `UPDATE agents` / `INSERT INTO agents` in `service/` (tests excluded) that sets `status` or `launch_mode`:
22 statements, read by hand (the review's census agrees). Seven write `stopped` or `none` as literals; registration
and dispatch take the value from the caller, and those two are the value-flow carriers: a literal scan cannot
bound them, so path 5 refuses the values at the boundary.

| # | write | trigger | defined agents reach it? | aify-env hears it? |
|---|---|---|---|---|
| 1 | `resident_loss.py:86` | `POST /agents/{id}/resident-lost` (`session_lease.py:204`), sent on a resident's clean exit (`mcp/stdio/resident-lost.mjs:45`) and by the hermes gateway-dead paths | yes | it sees the session end and publishes `offline` |
| 2 | `agent_sessions.py:329` | session control `cli_takeover` (`session_control.py:52`) | yes: only stop/restart/recreate delegate (`:66`) | no |
| 3 | `environments.py:233` | environment control `stop` (`environments.py:149`) | yes | no: `environment_controls` has no consumer (`aify-env/lib/plugins/aify-comms/claimer-answer.mjs:30-34`) |
| 4 | `agent_sessions.py:347`, `agent_stop_resume.py:57`, `environment_assignment.py:229-241` | operator stop, assign | **undefined at the check**, not unreachable: see path 4 | n/a while undefined |
| 5 | `dispatch.py:363`, registration `agent_registration_writes.py:195-237` | caller-supplied `agentStatus`, `status`, `launchMode` | yes | no |

## Fix per path

1. **A resident's loss rests it `offline`, unless it was already stopped.** In the resident branch of
   `_settle_lost_resident_when_no_transition`: a row for which `was_stopped(row)` (`nested_session_handback.py:52-54`)
   is true keeps today's write and transition `resident_to_stopped`, since that stop predates the loss and is someone
   else's. Any other row gets `status='offline'` with `launch_mode` untouched and transition `resident_to_offline`.
   `offer_handback` (`session_lease.py:252`) runs for both transitions, so the nested same-handle handback keeps its
   note and `resident_lost_reclaimable`. Unchanged: the current-bridge refusal (`session_lease.py:216`), the
   auto-return to managed, and the managed branch (`resident_loss.py:64`). **There is no send change.** A send to a
   resident in either state is stored without a run (`messages.py:174-189`; `send_preflight.py:94-99` refuses the
   absent resident before status). `offline` promises no later delivery; a future-delivery queue would be a separate
   policy.
2. **Refuse `cli_takeover` for a defined agent, with a 409 before any effect.** Delegating it as a D9 stop is wrong
   on aify-env 2ae603de: the stop fact publishes `stopped` ahead of any process fact (`lib/agent-state.mjs:57`), and
   only a successful start clears it (`lib/agent-lifecycle.mjs:207`; it is set at `:224`). The operator's CLI would
   then run under a `stopped` row, which is the disagreement B2 removes, and the stop also blocks automatic starts
   (`:110-111`). The supported takeover of a defined agent already exists: flip its definition to resident in
   aify-env, and comms follows (`session_mode_switch.py:190-227`). The 409 names that path. **The fence is the
   takeover handler's own transaction:** for `cli_takeover` only, `control_session` takes `BEGIN IMMEDIATE` before its
   definition check and holds it through the undefined agent's legacy writes, so a definition push (same lock) lands
   wholly before or wholly after. An undefined agent keeps every legacy effect: session state, interrupt, spawn
   cancellation, terminal stop controls, the response and the broadcast (`session_control.py:75-138`). The stop,
   restart and recreate branches are unchanged: they call `delegate` with no transaction open, and `delegate` keeps
   its own `BEGIN IMMEDIATE` (`lifecycle_delegation.py:71`). No shipped client sends `cli_takeover`: the dashboard
   sends stop/restart/recreate (`new_dashboard/agent-session-actions.mjs:314-326`), and the bridge sends
   restart/recreate (`mcp/stdio/lifecycle-tools.mjs:164`). AGENTS.md:112 documents a "Pause for CLI" button that the
   dashboard does not have; that doc line is corrected in the same change. A queued-stop takeover protocol (note
   carrier, attach and no-overlap receipt, wake policy) is not proposed; it would need aify-env's own design first.
3. **Refuse environment `stop` when its affected set holds a defined agent.** The affected set is the stop's own
   population: every agent with ANY `agent_sessions` row for that environment (`environments.py:231-248`), defined on
   any machine. It is not forget's governing-store query (`:171-174`). `BEGIN IMMEDIATE` is taken before the
   environment read, for `stop` as `forget` already does (`:157-160`), and held through every write. A definition push
   takes the same lock (`routers/agent_definitions.py:30,103`). So a definition either commits first and is seen, or
   commits after the stop. The 409 rolls back before the control insert, the environment status, the session `lost`
   writes, the agent writes, the cache invalidation and the broadcast. The per-agent D9 fan-out is not covered by
   this proposal.
4. **Retained, named: the C5 timing exception.** `assignment_for_its_host` rolls back on an undefined miss
   (`definition_requests.py:157-161`), and `delegate` answers None on an unfenced check (`lifecycle_delegation.py:63-69`).
   A definition can commit between that miss and the legacy write (`environment_assignment.py:229-241`,
   `agent_stop_resume.py:57`, `agent_sessions.py:347`); the route allows this case (`environment_assignment.py:81-84`).
   B2 keeps it under its existing operator authority (the operator acted while the agent was undefined) and does not
   change C5. B2 therefore does not close every defined-agent mismatch: one written in that window remains possible.
   Removal is outside this exception: it has its own in-transaction recheck (`agent_remove.py`, refusal fence).
5. **Refuse explicit `stopped`/`none` from callers, 422, before every effect.** Normalize with strip and lower.
   Registration refuses `status` `stopped` or `launchMode` `none` at the top of `_register_agent`
   (`routers/agents/registration.py:140`), before the tombstone gates, the console-adoption return (`:259`), the
   manual-takeover return (`:314`) and the upsert. `PATCH /dispatch/runs/{id}` refuses `agentStatus` `stopped` at
   entry (`dispatch.py:257`), before run updates, settlement and the agent write (`:293-365`). An omitted field and
   every other value are unchanged. No bundled caller sends either value; that does not show no public caller does,
   so the 422 names the operator route instead.

## Witnesses (each arm restored by byte copy)

- **W1:** a resident with no auto-return, not previously stopped: resident-lost stores `status='offline'` with
  `launch_mode` byte-equal to before, serves `offline`, and a real `POST /messages` to it returns the same disposition
  as today (stored, no run, wake failed), asserted apart from status. Every case asserts the stored `status`,
  `launch_mode` and `status_note` as well as the served status. Prior-stop controls, one per marker: a status-only
  stop (`stopped`, `launch_mode='detached'`) must store `stopped`; this is the case where a removed guard would show,
  because nothing else makes it read stopped. A launch-mode-only stop (`idle`, `none`) must store what today's write
  stores. Other controls: a non-current bridge is ignored; a managed agent rests `active`/`detached`; a nested
  handback still answers `resident_lost_reclaimable`. Arms: restore the unconditional stop; drop the `was_stopped`
  keep (the status-only control goes red); skip `offer_handback` on the new transition.
- **W2:** `cli_takeover` of a defined agent answers 409, and nothing persists: the session, runs, spawns, terminal
  controls and the agent row are byte-equal, and there is no broadcast. Control: an undefined agent gets every
  legacy effect. Arm: remove the refusal.
- **W3, population:** a stop is refused with byte-equal tables and no broadcast for each of: a local defined agent,
  an agent defined on another machine with an old session here, a mixed population, and a definition committed
  before the handler starts. Control: undefined-only still disables. Arm: use forget's query.
- **W3, the race:** this is what tells the fenced check from a check outside the fence. Two connections and events;
  no sleeps, and no wait used for ordering (each wait has a long deadline only so a broken run fails instead of
  hanging). Agent X is undefined and has an old session on E.
  1. The stop handler runs with its guard read wrapped: after the read returns, it sets `guard_read` and waits for
     `rival_settled`.
  2. A rival thread on its own `sqlite3` connection waits for `guard_read`, then tries `BEGIN IMMEDIATE` with
     timeout 0. If the database is locked, it sets `rival_blocked` (which sets `rival_settled`) and retries with a
     long busy timeout. Once in, it inserts X's definition, commits, records `rival_commit`, and sets `rival_settled`.
  3. The stop takes its fence, if it does not already hold it, and its first protected write (the
     `environment_controls` insert) records `stop_first_write`; its commit records `stop_commit`.

  Fenced code: the rival is blocked, the stop disables X legally because X is undefined under the lock, and the
  recorded order is `stop_first_write < stop_commit < rival_commit`. Check-outside-fence arm: the rival is not
  blocked and commits first, and the stop then writes to a defined X. The assertion is: if `rival_commit` precedes
  `stop_first_write`, the stop must have answered 409 with byte-equal tables. That arm goes red.
- **W2b:** the W3 race, run against the takeover handler's guard read. Arm: check the definition before `BEGIN
  IMMEDIATE`.
- **W5:** `status`/`launchMode`/`agentStatus` as `stopped`, `" Stopped "`, `NONE` are refused on each registration
  branch and on PATCH, with the run, result, settlement and agent rows unchanged. Controls: omitted fields, `idle`,
  `detached`. Arms: move the dispatch guard to just before `UPDATE agents`; guard only the ordinary upsert.

## ASSUMED until built

- That `send_preflight` reaches the resident-bridge refusal before status for both the `stopped` and `offline` rows
  (read at `:94-99`, not executed). W1 pins it.
- That nothing outside the three repos calls `cli_takeover` or registers with `none`/`stopped`.
