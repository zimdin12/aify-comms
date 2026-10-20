# 1. What comms reads after the switch (revision 2)

Revision 2 answers comms-senior-dev's shape input (reviewqueue-219/c8-n2-authority-1792516661010) and supersedes
5e6c6654.

## Three typed facts, never collapsed

| fact | values | source |
|---|---|---|
| **availability** | `fresh`, `stale`, `restored`, `unavailable`, `absent` | the mirror: the publisher's state plus `age_class` on comms' own receipt clock |
| **busy** | `true`, `false` | the record's top-level `busy`, as aify-env decided it |
| **handoff** | none, or the open handoff run ids | `dispatch_runs` (below), comms-owned |

**Busy comes from the record's own field.** The receiver already validates top-level `busy`
(agent_state_shadow.py `_record`, via `_flag(record['busy'])`). The operational projection gains `Record.busy` from that
field. It never comes from `busyIf` alternatives, a comparison variant, or the raw evidence bytes, which `RAW_LIMIT`
can drop. aify-env's `turnIsBusy` decides it: a verified lifetime's open turn has no timer, and an unverified one uses
aify-env's own fallback, which stays aify-env's design.

**UNKNOWN, the operational predicate.** An agent's delivery eligibility is unknown when any of these holds:
- no publisher on its machine holds a record of it;
- every record of it is `stale`, `restored`, `unavailable` or lost;
- some publisher on its machine is unreadable;
- the mirror binding failed.

Unknown is never read as `false`, as `available`, as legacy idle, or as a fallback to `agent_turn_state`. The displayed
word for it is `unknown` (Q1). Q3's proven-absent wake handle still reads `offline`.

## `holdsDelivery`

- **true:** a fresh record says `busy`, under a conflict either fresh record says `busy`, or an open handoff exists for
  a run other than the one being claimed.
- **false:** every fresh record says not busy, eligibility is not unknown, and no other open handoff exists.
- **unknown:** otherwise.

Deleted from the operational path: the stale-record open-turn disjunct and `strictExpiry` (N2), the delivery-turn
disjunct with its `turn_is_still_live` timing and renewal read, and `busy_consumers.other_work`'s 120-second turn
freshness. comms keeps no turn timer.

## Message handoff: closing the gap before aify-env sees the turn

`runOccupied` is not enough:
- `_get_dispatch_state_for_agent` counts claimed, running and queued runs only (dispatch_state.py:23-44).
- `_current_active_run_row` excludes `delivered` on purpose (active_run_lookup.py:30-45).
- A no-reply delivery can finish its run while the assistant still works (dispatch_launch.py:68-79).

**Paths that take a run out of claimed/running before aify-env has observed the turn it causes:**

| mode | path | status written |
|---|---|---|
| claude channel (resident and managed) | channel-dispatch-receipts.mjs `markDispatchDelivered`, after `emitChannel` | `delivered` when a reply is owed; **`completed` at delivery when none is** (the auto-complete) |
| managed hermes TUI and gateway | hermes-run-reporting.mjs `markRunDelivered` | `delivered` |
| console/PTY input, non-turn runtime | console_input_queue.py:108-109 (`running` only for `RUNTIMES_THAT_TRACK_A_TURN`) | `delivered` |
| reply linked to the run | dispatch_run_state.py:103-116 completes queued, delivered, and channel/resident/terminal claimed or running runs | `completed` |
| inbox read of the run's message | inbox_read_receipts.py:46-55 completes claimed or running runs | `completed` |
| batch extras merged into one turn | dispatch-loop.mjs `finalizeBatchedExtras` | `completed`, with run[0] still running |
| stale delivered reconciler | dispatch_queue.py:100-125 | `completed` |

For native-managed runs (dispatch-loop.mjs: `running` at start, `completed` at the outcome), the run itself stays
claimed or running across the turn, so `runOccupied` covers them with no handoff.

**The handoff fact** is a small persisted row under dispatch ownership, beside `dispatch_runs`: a column set, or a
`dispatch_handoffs` table keyed by run id.
- **Opened** by the transport's delivery write (event `delivered`, whether it sets `delivered` or `completed`) in a
  mode from the first three rows above. It records the
  run id, the agent id, the agent's lifetime when the record names one, and the **turn anchor seen at handoff**: the
  `startedAtUs` of the agent's current fresh record, or 0 when its turn is closed or unknown.
- **Resolved by one of:**
  - a later applied record of the same lifetime whose open turn has a `startedAtUs` different from the anchor: a turn
    started after the handoff;
  - an outcome settlement of the run:
    - a reply linked to it (dispatch_run_state.py);
    - its message read through the inbox;
    - `failed` or `cancelled`;
    - an operator action.

    **Not** the transport's own delivery write: the auto-complete at delivery opens the handoff, it does not close it.
    **Not** the stale-delivered reconciler (dispatch_queue.py), which closes runs by age.
  - the agent's lifetime changing (the process that was handed the message is gone).
- **No time ends it.** Comparing `startedAtUs` values for equality uses aify-env's clock as an opaque identity, never
  as a duration.
- **Its own run never blocks it,** and a queued run has none. It is not "every delivered or reply-owed row": a
  lingering delivered row whose handoff is resolved occupies nothing.
- **Mid-turn injection** (steer) on a channel is delivered with the next turn (channels documentation: "delivered
  together on the next turn"). The new `startedAtUs` resolves it. A harness that swallows the message without a new
  turn leaves the handoff open until the run settles or the lifetime changes. That is a stuck agent made visible, never
  a second message into the gap.

## Readers

| reader | after the switch |
|---|---|
| send-time queue (dispatch_launch.py:84-95) | queue when `runOccupied`, queued runs exist, or `holdsDelivery` is not false |
| claim gate (dispatch_claim.py:364-384) | **unknown withholds before steerability, for every claim, steer included.** true holds unless a claimable steerable run exists (today's carve-out, applied only to a known true). false proceeds |
| reminder skip B21 (dispatch_sweeps.py:279-281) | skip when `hasActiveRun`, when `holdsDelivery` is unknown, or when the agent is busy and this run's handoff is resolved (it is working on other work, or on this run after picking it up) |
| stranded guard B26 (dispatch_lifecycle.py:139-145) | leave the run when its own handoff is open, when the agent is busy, or when eligibility is unknown |
| word and promotion (records.py `_status_with_dispatch`) | the one view applies row 5, so a running run shows `working`, on every path |

`busy_consumers.claim_candidate` cannot be promoted unchanged. Its conjunction (busy_consumers.py:40-44, 69-72) turns an
unknown hold with steerable true into a known false. The claim rule above replaces it.

## Run correlation: two options for review

aify-env's turn events carry no run id (env lib/turn-events.mjs:21-22).
- **A (recommended): no run id in aify-env.** comms correlates through its own handoff rows. The handoff needs only
  `startedAtUs`, which aify-env already publishes. B21 and B26 read the handoff state for the run.
- **B: aify-env's turn carries the reporter's `runId`.** That is a schema change in env, comms and wrapper (every copy).
  It gives exact correlation, but run-correlated readers would still need the handoff for the gap before the turn
  starts.

**Handoff coverage needs A's data only.** B adds exactness to B21 and B26, and is not required for the gap.

## Restart: restore without claiming knowledge

At service start, before the claim, send and ingest routes are served:
- each non-retired publisher and its rows are loaded from `agent_state_shadow_publishers` and `agent_state_shadow_rows`;
- `record_json` holds the full validated record, so top-level `busy` is restored too;
- the cursor and data generations stay distinct, and the stored `data_applied_at` is kept unchanged.

**Restored is not fresh and not complete.** Every restored publisher is `restored`. Its rows are shown on the word
paths as last-known, marked stale, and its agents' delivery eligibility is unknown until that publisher's next applied
publication, which replaces it under the normal rules. The saved tables do not record `Mirror.complete` (a snapshot at
publication 1 then changes at 2, and changes at 1 then 2, can leave identical rows and cursor), so the reload claims
nothing about completeness.

**Edge cases:**
- **Retired cursor:** absent from the mirror.
- **Unreadable row, or a table read failure:** that publisher, or the process, loads `unavailable`. Quarantine works as
  in B5.
- **Empty tables:** an empty mirror.
- **A generation that went backwards:** the stored cursor is the highest applied, and `_order` keeps refusing
  lower ones.
- **A publication accepted while the reload runs:** cannot happen, because ingest is not served yet. The reload also
  refuses to overwrite a key already present in the mirror.

No bootstrap timer exists. After a restart, delivery waits for each host's next publication, and the restored rows
only keep the display honest meanwhile.
