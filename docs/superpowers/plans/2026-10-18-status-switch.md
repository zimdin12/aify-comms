# The status switch to aify-env: 0.9.8 (2026-10-18, revision 2)

The 0.9 plan's P6-P8 (`2026-10-02-aify-env-owns-agent-state.md`; contracts C6-C8 in its P0 document). Revision 2
answers comms-senior-dev's REVISE of 39125cbf, findings R1-R4.

## Where it stands, measured 2026-10-18 16:20Z

- B1 is live. aify-env a66d011 holds the residents role.
- The live shadow (`GET /agent-state/shadow-report`) has made 41,687 partial comparisons of the status word:
  - 30,129 agree and 11,558 disagree;
  - another 35,618 are unavailable.
- The count is process-cumulative. It has `coverageComplete=false`, `fullQualifiedComparisons=0` and null versions,
  and the other nine C8 decisions are not instrumented at all. The recorder keeps one sample per source.

## Rulings, now in C6

These are settled (DECISIONS c8756959, P-1 1791194105933), and C6's word table changes to match:
- **Q1:** C6 row 2b, no record, reads `unknown`, not `offline`.
- **Q3:** a resident whose wake handle is proven absent reads `offline`. This is one override in the pure comms view,
  applied once for push and poll alike:
  - aify-env's record and its verified busy stay intact underneath;
  - `deliverable` and `runOccupied` stay separate outputs;
  - an aify-env that is not reporting is not proof of a missing handle, so that case stays `unknown`.
- **Q4:** push and poll call the same `commsView`.
- **Q2:** pre-0.9 launchers are `unbound`.
- **P-1:** verified open turns have no wall-clock cap.

## Order

1. **S1, a triage table (comms).** Disagreements are counted by (source, mode, comms word, env word), with one
   sample per key. It is used for triage only, never for admission (R2).
2. **S2, the C8 recorder, complete (comms; this is B6's comms half).**
   - **All ten decisions, each its own owner:** status word, busy, send-time queue, claim, worker readiness, live
     counts, reminder skip, stranded-reply guard, running-run promotion and stale-turn ceiling.
   - **Each evaluation computes both answers on the same rows.** It stores the raw inputs of both sides, the exact
     env, wrapper and comms build ids, and `turn.ageMs`. aify-env publishing `ageMs` and its own build is B6's env
     half.
   - **Persisted in the service database, in windows (R3).** A window is bound to one exact version triple, and its
     counts are deltas within the window. A service restart, a lost publication or a version change closes the window
     and opens a new one. A closed window is never merged, so a gap shows up as a gap, and history is kept.
   - **Classes are input predicates (R2).** A class is a named predicate over the recorded inputs, for example "Q3:
     resident, record present, wake handle proven absent". Every disagreement is attributed to the predicates it
     satisfies. One that satisfies none, or more than one, is unresolved.
     - Expected policy differences are counted separately from bugs.
     - Witness: two causes that share one S1 key, a ruled no-wake `offline` and an `offline` that still has a wake
       handle, land in different classes.
3. **S3, classify, then repair in aify-env (B2-B5).**
   - Every predicate is labelled a bug, a ruled policy or unresolved. Volume orders independent repairs only;
     measurement dependencies come first.
   - B3 reconciles the authoritative mode rather than copying the legacy engine.
   - Each repair is re-measured in a new window on the repaired versions.
4. **Gate (R1), C8 as written.**
   - One full day of continuous windows on one version triple, with a nonzero denominator for every one of the ten
     owners.
   - Zero unresolved, zero open bugs, and zero open B-gaps.
   - Every policy difference is one that has been ruled.
   - The deterministic test for each class the fleet did not show, from C8's list.
   - An owner that is uninstrumented, unavailable or has zero evaluations fails the gate.
5. **S4, the switch (P7), comms only.**
   - The witnesses (289373cc) are green on today's code first.
   - Then `commsView` serves every reader, and `holdsDelivery` replaces `_turn_busy_holds_delivery` in the busy,
     queue and claim gates.
   - **Launchers keep posting to both aify-env and comms (R4).** The legacy routes keep APPLYING throughout the
     interval, so `agent_turn_state`, `agent_status_state` and `agent_hook_order` stay current. The old engine keeps
     computing in a reverse shadow: the S2 recorder compares old against new.
6. **The legacy interval: the 0.9.8 release.**
   - The 0.9.8 tag carries the switch. Deletion comes no earlier than 0.9.9, and only after a day of 0.9.8 windows
     with zero reverse-shadow disagreements outside ruled classes.
   - **Rollback inside the interval** reverts S4's commits (the view and the gate rewiring) and rebuilds the
     container. Launchers are unchanged, so nobody relaunches. The data is complete, because the legacy tables
     were written all along.
   - A test drives a turn born after the cutover, then rolls back over the same history. It checks hold and
     release, queue and claim, and run ownership under the legacy readers.
7. **S5, the deletions (P8), 0.9.9.**
   - A DB backup comes first. After the tables are deleted, reverting the source does not restore the data, which is
     why S5 waits for the interval.
   - Order:
     1. Launchers post only to aify-env, and Steven relaunches.
     2. The legacy routes switch to accept-and-ignore (C7).
     3. The engine, detectors, tables and shadow recorder are deleted, including the S2c-E evidence:
        `status_shadow_evidence`, `status_shadow_evidence_gaps`, `status_shadow_window_tombstones`, the windows'
        evidence columns, `shadow_evidence.py`, `shadow_evidence_store.py` and `GET /agent-state/shadow-evidence`.
   - Each deletion needs proof that no live route reaches it (the C2 discriminator).
   - pi and opencode stay deprecated and kept.

## What would prove this wrong

- A disagreement no predicate attributes from the recorded inputs: the switch waits, and C2 gains a row.
- An owner whose denominator stays at zero on the real fleet: that owner needs a deterministic witness and a ruling
  before the gate.
- A delivery decision that changes across S4, or after rollback, without a ruling: a witness goes red.

## Classification log (S2, live)

Read from the live service (`/agent-state/shadow-report`), counted per observation rather than per agent. One agent
re-evaluated every few seconds can therefore dominate a class.

- **2026-10-18 19:00Z, build 88f3708c. `comms-only-stop` was ONE agent, and it was not an operator stop.**
  - The class held 289 of 405 disagreements (71%), all of them ef-senior-dev. Its wake-disabled flag
    (`launch_mode='none'`, status `offline`) came from an April environment removal (`routers/environments.py`), not
    from the operator.
  - The operator ruled: make it startable. The comms resume cleared the stale flag (`launch_mode` detached, status
    idle), and the class stopped growing (1131 before and 1131 after 465 more comparisons).
  - Operator stops of defined agents already go through aify-env's journal (`lifecycle_delegation.py`, D9c).
    So B2's remaining scope is the system paths that set `launch_mode='none'` without one: environment removal
    and CLI takeover. Each is either a fact aify-env should hear about, or one comms should stop deriving `stopped` from.
- Still open:
  - env `blocked` vs comms `working` or `online` on residents: aify-env sees awaiting input that comms does not. No
    class yet; which side is right is not established.
- **2026-10-18 23:20Z, build 88f3708c. Two keys explained; an earlier B3 attribution withdrawn.**
  - resident `offline` vs env `online` (4,529 of 6,243): pc-manager. comms reads `hermes-missing-handle`
    (`liveness.py`): its bridge restarted at 19:50Z after my %TEMP% cleanup had deleted its
    `aify-hermes-gateway-` marker, which the bridge reads only at load (`hermes-gateway-config.mjs`). The gateway
    on 8868 was listening throughout. This is the Q3-ruled word, so the key needs the Q3 class, and comms' recorded
    inputs must carry the missing handle: `bridge_stale` alone cannot tell it from a dead bridge. The marker was
    rewritten from the gateway's own token (control: comms-senior-dev's surviving marker reproduced byte-equal); it
    takes effect at pc-manager's next bridge start.
  - resident `offline` vs env `available` (60): llama-manager. comms keeps the session mode of its last run
    (resident, 10-12 18:52Z); its definition became managed at 19:01Z. The definition is the authority: B3, no
    operator decision needed. dashboard-senior-dev, named here earlier, is defined resident and is not in B3.
