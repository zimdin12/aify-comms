# 2. The sandbox gate (SW3a and SW3b, revision 5)

**Two stages.** SW3a builds the harness: the stand-in, the frozen cases and their REDs. SW3b qualifies: it runs the
three attempts against one exact pinned pair, the SW5 candidate commit (built and reviewed on a branch, not landed)
and an aify-env commit. A green SW3b gates landing that candidate commit (SW5L), never building it. A candidate that
changes after qualification is a new pair and qualifies again.

The gate runs on the env-against-comms runner (branch 098/env-comms-sandbox). That runner's own ownership and cleanup
contract (a5c658ff) needs its own source review; it is not accepted by reference here, and the gate does not run
until it is accepted.

**The stand-in agent** is a defined managed agent with a scripted launcher. It registers, claims runs through
`/dispatch/claim`, reports `turn-start` and `turn-end` to aify-env's real `POST /agents/:id/turn-event` for its current
lifetime, and logs each delivery.

It is bounded protocol evidence. Real Claude and Hermes queueing (page 1's runtime table) is not exercised here: it
rests on the cited documentation and code, and on the switch watch.

## Cases

The expected answers are frozen per case before the run. Every RED names a mutation that reaches the predicate, kept
apart from syntax errors, missing fields and unreached branches.

The cases run against the wired candidate. The legacy tables still receive writes there (page 3 keeps the rollback's
inputs live), so the first two cases set a legacy answer against the env record to prove the rewired readers ignore
the legacy one.

The cold-start case's inputs are frozen as: the stand-in is defined (bound), has no live backing, and comms holds no
record for it from any publisher, fresh or stale. A preceding record of an earlier lifetime is a different input,
not this case; its answer is the truth table's, and no lifetime correlation is invented to bridge the two.

| case | witness (expected answer) | RED, and the mutation that reaches it |
|---|---|---|
| fixture where legacy and candidate differ: `agent_turn_state` busy=1, fresh env record idle | delivered at once | the reader still takes the legacy table (it holds) |
| the converse: legacy busy=0, fresh env record busy | held; delivered on the first claim pass after comms accepts the idle publication | the reader takes the legacy table (it delivers mid-turn) |
| busy, steerable channel run | steered | the carve-out is removed |
| fresh idle holder plus a stale second holder on the same machine | eligibility unknown: withheld, steer included | stale holders are ignored (known false: delivered) |
| aify-env paused | after 180 s of receipt silence: `unknown` with a stale last-known note; sends queue; claims withhold, steer included | unknown read as false, or steer checked before eligibility (the steerable run is delivered) |
| aify-env resumed | queued work drains on the first claim pass after comms accepts a fresh publication | the queue never drains |
| default send (not `queueIfBusy`) while unknown | queued, not claimed | the claim gate withholds only `queueIfBusy` runs |
| comms restarts while the agent is idle | before aify-env's next complete snapshot: `restored`, eligibility unknown, a send queues; after it: delivered | restored rows promoted to fresh or complete (the send is delivered before the snapshot) |
| comms restarts mid-turn | `restored`, eligibility unknown, word `unknown` with a last-known note | the restore removed (no last-known note), or promoted (eligibility known) |
| restored, then a same-generation changes publication | still `restored` and unknown until a complete snapshot is accepted | changes treated as completing the restore |
| cold start: a send to the defined stand-in with no live backing | a spawn request with an empty initial message; the run stays queued; the claim withholds until comms accepts the new lifetime's record, then the run is claimed | the send-time hold skips the backing step (no spawn request; the run waits forever) |
| a run is running on an idle record | the word reads `working` | promotion skipped at the view |
| stop, then restart, through aify-env | `stopped` (a send is stored, not run), then `online`, with queued work drained | the view's stop row skipped; restart leaves `unknown` |

Two obligations move out of the sandbox:
- **No comms timer**, as a unit witness on the pure view, with two observables kept apart:
  - *Turn age.* Publisher availability and the receipt-age class are held fixed (`fresh`) as inputs, while the
    turn's age and the evaluation time vary by hours. The answers must be identical. A reintroduced `strictExpiry` or
    `turn_is_still_live` changes the later answer and reaches this assertion.
  - *Receipt age.* The turn is held fixed while receipt age crosses 180 s. The answer must move from known to
    unknown. A view that ignores the receipt-age class reaches this assertion.

  Legal receipt aging therefore never reddens the turn witness, and each mutant reaches its own assertion.
- **aify-env's unverified-lifetime fallback** is aify-env's, tested in aify-env.

## Pass rule

- Three scheduled attempts per pinned commit pair (the SW5 candidate commit, the aify-env commit).
- **Green** means every required frozen case, and the admitted runner's cleanup, passes in all three attempts. One
  failed attempt fails the gate for that pair.
- Every attempt is kept, failures included. "Run until three greens" is not the rule.
- Mutation validity, the reached assertion, the restoration and the baseline are recorded separately.
- The 180 s silence case takes about four minutes, with no clock seam added for it.
