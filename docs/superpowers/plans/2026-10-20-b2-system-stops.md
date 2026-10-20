# B2: the stops aify-env never hears about (2026-10-20, draft for review)

Scope, from `2026-10-18-status-switch.md` (the 19:00Z entry): operator stops of DEFINED agents already go
through aify-env's journal (D9c, `lifecycle_delegation.py:53`). B2 is every other write that makes comms
derive `stopped`/`disabled` (`status_inputs.py:239-242`: `status='stopped'` OR `launch_mode='none'`) with no
journal entry. Not built; this page asks for GO/REVISE.

## The population, and how I know it is all of it

A scan of every `UPDATE agents` / `INSERT INTO agents` in `service/` (tests excluded) that sets `status` or
`launch_mode` found 22 statements, each read by hand. Positive control: it found both known operator paths,
`agent_stop_resume.py:57` and `agent_remove.py:55`. Negative control: a value nothing writes
(`launch_mode='paused'`) returned 0. Among the 22, 11 keep a stop, lift it or write a non-stop value, 2 copy a row (rename) or
start a spawn, and 7 write `stopped` or `none`; two more take the value from the caller. Raw SQL only:
no ORM path exists.

| # | write | trigger | defined agents reach it? | does aify-env hear it? |
|---|---|---|---|---|
| 1 | `resident_loss.py:86` stopped+none | `POST /agents/{id}/resident-lost` (`session_lease.py:204`); sent on a resident's CLEAN EXIT by `mcp/stdio/resident-lost.mjs:45` (`server.js:446`) and by the hermes gateway-dead paths | yes | it sees the session end itself and publishes `offline` |
| 2 | `agent_sessions.py:329` stopped+none | session control `cli_takeover` (`session_control.py:52`) | **yes**: only stop/restart/recreate delegate (`session_control.py:66`) | no |
| 3 | `environments.py:233` offline+none | environment control `stop` (`environments.py:149`), every agent with a session there | **yes** | no: the `environment_controls` row has had no consumer since v0.6.2 (`aify-env/lib/plugins/aify-comms/claimer-answer.mjs:31-33`; no `environments/controls` caller in aify-env `lib/` or `mcp/stdio`; the same grep finds `terminals/controls/claim`) |
| 4 | `agent_sessions.py:347`, `agent_stop_resume.py:57`, `agent_remove.py:55`, `environment_assignment.py:231` | operator stop, remove or assign | no: each delegates or hands to the host first (`session_control.py:66`, `session_ops.py:68`, `identity.py:179`, `environment_assignment.py:85`) | n/a: aify-env publishes no undefined agent (live: 36 published agents, the only undefined ones were two test agents removed today and one dashboard check) |
| 5 | `dispatch.py:363` `status=?`; registration `agent_registration_writes.py:197-237` `status`, `launch_mode` | caller-supplied (`VALID_STATUSES` includes `stopped`, `status_engine.py:18`; `models.py:178,182`) | yes | no |

Live specimens today: sc-manager and mc-manager (defined residents) are comms `stopped`/`none` from path 1
(notes from 10-17 and 10-18), and aify-env publishes both `offline`. ef-coder-lead and ef-tech-lead are
`none` from April, undefined, so they have no env row.

## Fix per path

1. **comms stops deriving a stop from it.** A resident closing its own CLI is not an operator stop; aify-env's
   word for it is `offline`, and the operator restarts it by launching the CLI. Rest at `offline` and leave
   `launch_mode` as it was. `offer_handback` already does this for one sub-case (`nested_session_handback.py:95`).
   Behaviour change for review: a send to a closed resident is refused today (`resident_loss.py:47-51`
   records that `stopped` hard-blocks delivery; for residents this is ASSUMED, and the witness pins it). After
   the fix it queues to the inbox, the way it does for an offline resident.
2. **Report it to aify-env.** A takeover of a defined agent is an operator stop of its worker: delegate it as
   D9 `stop` with the takeover note and write nothing to the row. Undefined agents keep today's write.
3. **Refuse it for defined agents** (409 naming them), the way `forget` already refuses while definitions
   govern (`environments.py:167-181`). The control is never drained, so today the button marks running agents
   disabled while they keep running. Alternative, one commit away: delegate a D9 `stop` per defined agent.
4. **Leave it.** No env row exists to disagree with.
5. **Refuse `stopped` and `none` from callers**, as `attributes.py:61` (D12) already does for self-reports. No
   sender uses either value today (`dispatch-loop.mjs` sends idle/working; `registration-tool.mjs:191,359`
   default to `detached`), so this closes a hole and changes nothing that runs.

## Witnesses (one per obligation; each arm restored by byte copy)

- **W1:** a resident's clean-exit resident-lost serves `offline` with `launch_mode` unchanged; control: a managed
  agent still rests `active`/`detached`. Arm: put `'stopped'`/`'none'` back at `resident_loss.py:86`.
- **W2:** `cli_takeover` of a defined agent leaves one pending D9 `stop` request and no `none` on the row; control:
  an undefined agent's takeover still writes the comms stop. Arm: drop the delegate call.
- **W3:** `stop` on an environment with a defined agent bound answers 409 and changes no row; control: with only
  undefined agents it still disables them. Arm: remove the defined-agent check.
- **W5:** dispatch `agentStatus: "stopped"` and registration `launchMode: "none"`/`status: "stopped"` are
  refused; control: `idle` and `detached` still pass. Arm: remove each refusal.

## Not determined

- Whether a D9 `stop` journal entry (path 2) blocks the operator's own CLI attaching to the same session
  handle afterwards. Needs reading aify-env's stopped-agent handling before W2 is written.
- What clears sc-manager's and mc-manager's stale rows after the fix: the next resident registration
  (`agent_registration_writes.py:111` keeps `stopped`), or a one-off correction. Proposed: the fix applies only to
  new events, and the two rows are cleared once by hand on the operator's word.
