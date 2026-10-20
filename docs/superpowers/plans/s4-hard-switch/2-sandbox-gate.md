# 2. The sandbox gate (SW3, revision 3)

The gate runs on the env-against-comms runner (branch 098/env-comms-sandbox). That runner's own ownership and cleanup
contract (a5c658ff) needs its own source review; it is not accepted by reference here.

**The stand-in agent** is a defined managed agent with a scripted launcher. It registers, claims runs through
`/dispatch/claim`, reports `turn-start` and `turn-end` to aify-env's real `POST /agents/:id/turn-event` for its current
lifetime, and logs each delivery.

It is bounded protocol evidence. Real Claude and Hermes queueing (page 1's runtime table) is not exercised here: it
rests on the cited documentation and code, and on the switch watch.

## Cases

The expected answers are frozen per case before the run. Every RED names a mutation that reaches the predicate, kept
apart from syntax errors, missing fields and unreached branches.

| case | witness (expected answer) | RED, and the mutation that reaches it |
|---|---|---|
| fixture where legacy and candidate differ: `agent_turn_state` busy=1, fresh env record idle | delivered at once | the reader still takes the legacy table (it holds) |
| the converse: legacy busy=0, fresh env record busy | held; delivered on the first claim pass after comms accepts the idle publication | the reader takes the legacy table (it delivers mid-turn) |
| busy, steerable channel run | steered | the carve-out is removed |
| fresh idle holder plus a stale second holder on the same machine | eligibility unknown: withheld, steer included | stale holders are ignored (known false: delivered) |
| aify-env paused | after 180 s of receipt silence: `unknown` with a stale last-known note; sends queue; claims withhold, steer included | unknown read as false, or steer checked before eligibility (the steerable run is delivered) |
| aify-env resumed | queued work drains on the first claim pass after comms accepts a fresh publication | the queue never drains |
| default send (not `queueIfBusy`) while unknown | queued, not delivered | eligibility checked only under `queueIfBusy` |
| comms restarts while the agent is idle | before aify-env's next complete snapshot: `restored`, eligibility unknown, a send queues; after it: delivered | restored rows promoted to fresh or complete (the send is delivered before the snapshot) |
| comms restarts mid-turn | `restored`, eligibility unknown, word `unknown` with a last-known note | the restore removed (no last-known note), or promoted (eligibility known) |
| restored, then a same-generation changes publication | still `restored` and unknown until a complete snapshot is accepted | changes treated as completing the restore |
| a run is running on an idle record | the word reads `working` | promotion skipped at the view |
| stop, then restart, through aify-env | `stopped` (a send is stored, not run), then `online`, with queued work drained | the view's stop row skipped; restart leaves `unknown` |

Two obligations move out of the sandbox:
- **No comms timer**, as a unit witness on the pure view. One frame is evaluated at two `at` values hours apart and
  must give identical answers. A reintroduced `strictExpiry` or `turn_is_still_live` changes the later answer, so the
  witness goes RED at the predicate.
- **aify-env's unverified-lifetime fallback** is aify-env's, tested in aify-env.

## Pass rule

- Three attempts per pinned commit pair.
- Every attempt is kept, failures included. "Run until three greens" is not the rule.
- Mutation validity, the reached assertion, the restoration and the baseline are recorded separately.
- The 180 s silence case takes about four minutes, with no clock seam added for it.
