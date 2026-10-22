# The gate owner's amendments to page 2 (SW3a, after review sw3a-source-1792542290852)

Page 2 is `docs/superpowers/plans/s4-hard-switch/2-sandbox-gate.md`. Each entry names the page 2 row it touches, what
page 2 says, what the gate does instead, and the review item behind it. These are the gate owner's proposals, and
each stands only once the reviewer accepts it. A case that carries an amendment's words in place of page 2's names the
entry (`amendment` in `scripts/sandbox/gate/cases.mjs`), and the self-check finds those words here.

## A1. legacy busy, env idle: the send is not steerable

- **Page 2:** "fixture where legacy and candidate differ: `agent_turn_state` busy=1, fresh env record idle" →
  "delivered at once".
- **The gate:** the send is `queueIfBusy` (not steerable).
- **Why:** a steerable send is delivered to a busy agent by either reader. On main the default send was delivered,
  so the case could not tell the two readers apart.
- The expected answer and the RED are page 2's.

## A2. "aify-env paused" is publication-only silence, measured in announced intervals

- **Page 2:** "aify-env paused" → "after 180 s of receipt silence: `unknown` with a stale last-known note; sends queue;
  claims withhold, steer included". Its RED is "unknown read as false, or steer checked before eligibility".
- **The gate:**
  - The relay (`scripts/sandbox/gate/relay.mjs`) refuses aify-env's `POST /agent-state` bodies. Its claimer
    heartbeat, spawn claims and lifecycle traffic still reach comms, so its environment stays in contact and sends
    still create runs.
  - The silence lasts three of the intervals aify-env last announced (`reportEveryMs`, decision 2), measured from
    its last applied report, plus a 5 s margin. An aify-env that announced none derives no silence, and the fixture
    fails.
- **The amended words of the env-paused case:** "after three announced intervals of publication silence: `unknown`
  with the prior record's word and application time; sends queue; claims withhold, steer included". Its RED:
  "unknown read as false (a send is delivered during the silence)".
  - The steer half of page 2's RED moves to A5. Under silence every holder is out of contact, so no fresh busy holder
    exists and a busy check placed first is never reached.
- **env-resumed and default-send-unknown** keep page 2's words and are realised the same way.
- **Why:** review R4 and R5.
  - Pausing the whole daemon also stops the claimer heartbeat. comms reads that environment offline at its own 90 s,
    and stores the 185 s sends with no run (the first attempt's evidence).
  - Page 2's 180 s is decision 2's specialisation for a 60,000 ms interval. It is no longer a constant.

## A3. The whole-daemon pause is a separate, unjudged fault history

- **Page 2:** none.
- **The gate:** the `daemon-lost` observation stops the whole aify-env daemon. It waits the derived silence, makes
  the same two sends, and records the word, the send answers and the environment's claim state. It is never a case
  and never judged.
- **Why:** review R4 keeps it apart from A2 rather than relabelling it. Whether a wired candidate must create runs
  for an agent whose environment is offline is a send-path question, outside these cases.

## A4. The stale second holder announces its interval

- **Page 2:** "fresh idle holder plus a stale second holder on the same machine".
- **The gate:** the second holder is a publisher key the harness posts once with `reportEveryMs: 1000`, the
  schema's lowest bound. It is judged stale after three of those intervals (3 s) since comms applied it, and
  aify-env's holder is judged in contact by its own announced interval.
- **Why:** review R5. Staleness is derived from an announcement, never from a fixed 180 s.
- The expected answer and the RED are page 2's.

## A5. A new case: unknown eligibility beside a fresh busy holder

- **Page 2:** none (page 1, "Every delivery route checks eligibility first").
- **The case:** `fresh-busy-beside-unknown`. aify-env's holder is fresh and busy. The stale second holder of A4 makes
  eligibility unknown, and a steerable send is made.
  - Expected: "eligibility unknown beside a fresh busy holder: withheld, steer included".
  - RED: "steer checked before eligibility (the steerable run is steered)".
- **Why:** review R6. No frozen case put a fresh busy holder beside unknown eligibility, so moving steer before
  eligibility reached no assertion.

## A6. Stop, then restart: a seeded run and two named checkpoints

- **Page 2:** "stop, then restart, through aify-env" → "`stopped` (a send is stored, not run), then `online`, with
  queued work drained".
- **The gate:**
  - **The queued work** is a named `queueIfBusy` run, sent while both readers hold (comms' legacy turn and
    aify-env's record busy) and read back `queued` before the stop. The send made while stopped must be stored, and
    must neither start the agent nor be delivered. It is not required to create a run.
  - **Checkpoint 1, the restart:** the first snapshot holding the new lifetime's record. The word read there must not
    be `stopped`, and the seeded run must be claimed on the first pass after that record is accepted.
  - **Checkpoint 2, the first turn:** the new lifetime's first turn has ended (the harness drives it). The word read
    there must be `online`.
- **Why:** review R7.
  - Comms' delegated stop leaves queued runs alone, so the seed survives the stop.
  - aify-env publishes a fresh lifetime as `turn-unknown`, which the candidate shows as `unknown`. So "online" cannot
    be read at checkpoint 1, and page 2's word is judged at checkpoint 2.

## A7. The last-known note is `agent.lastKnown = {status, appliedAt}`

- **Page 2:** "word `unknown` with a last-known note" (restart mid-turn) and "a stale last-known note" (A2).
- **The gate:**
  - `GET /api/v1/agents/<id>` carries `agent.lastKnown`, an object with:
    - `status`: the word comms showed for the last record it applied (`candidate_word`, so idle reads `online`);
    - `appliedAt`: that record's application time, the publisher's `data_applied_at`.
  - The judge compares both against the record the scenario read just before the silence or the restart.
  - A missing object, a bare truthy value, or prose in `statusNote` all fail.
- **Why:** review R8.
  - SW2's view already computes `last_known` as `(word, applied_at)`; this names its API form.
  - It is an interface SW5 must expose. On today's main there is no such field, so the cases fail there.

## A8. The cold start's population is constructed

- **Page 2:** "the stand-in is defined (bound), has no live backing, and comms holds no record for it from any
  publisher, fresh or stale".
- **The gate:**
  - The pair's relay refuses aify-env's publications from before aify-env starts. Its definitions and its claimer
    still reach comms, so the agent is defined, claimable, and recordless.
  - The harness reads the agent's records back as empty before the send.
  - After the stand-in has registered and polled through the hold window, the silence is lifted. The first record
    comms accepts is the new lifetime's.
- **Why:** review R3. Without this, aify-env had already published the defined agent as `available` with a null
  lifetime before the send, which is a different input.

## Composition with decision 2 (review R5)

- The review asked for c1cdda21. While this was built, comms main fast-forwarded to 36028554 (the report-interval
  commit, on c1cdda21), and aify-env main to 7a855db (on ccf9a7e). The harness is rebased onto comms main 36028554,
  so its branch carries decision 2's comms half as main does; nothing of the report-interval work is merged or copied
  into the harness.
- The harness touches decision 2 only at run time, through the pair under test:
  - it reads `reportEveryMs` from aify-env's bodies at the relay;
  - it posts the second holder's own announcement to comms.
- Against comms without the field (c1cdda21 and earlier), the second holder's announcement is refused with 422 and the
  A4 and A5 fixtures fail by name. Against aify-env without it (ccf9a7e and earlier), no silence is derived and the
  A2 fixtures fail by name.
- So these cases are meaningful only against a pair carrying decision 2 (approved design e4afd022): comms at or after
  36028554, aify-env at or after 7a855db0. SW3b's pinned pair is the SW5 candidate, built on such a comms, and such an
  aify-env.
- The receipt attempt in this folder runs comms 36028554 against aify-env 7a855db0. Their readers are still unwired,
  so every case's RED there is the unwired readers'.

## A9. Stop, then restart: queued work is failed by the stop and kept in the inbox (owner ruling 2026-10-22)

- **Page 2:** "stop, then restart, through aify-env" → "`stopped` (a send is stored, not run), then `online`, with
  queued work drained". RED: "the view's stop row skipped; restart leaves `unknown`".
- **The gate:** "`stopped` (a send is stored, not run), then `online`; queued work failed by the stop, never delivered,
  still unread in the agent's inbox, and a notice stored for its sender". This replaces A6's drained seed. A6's checkpoints and its stored send stay.
  - The seed is the named `queueIfBusy` run from A6, read back `queued` before the stop.
  - After the restart and a whole claim pass after the first turn, the seed must never be claimed and the stand-in must
    never receive it: no successful frame may show it with a `claimed_at`, including after an earlier `failed`
    frame. Its last status must be `failed`. Its message must still be unread in the agent's inbox, read with `peek`
    so that reading it marks nothing read.
  - A `handoff` event on the seed names its sender: the stored notice. Live delivery of that notice, and the sender's
    reply obligation, are outside this case. They depend on the sender, and the fixture's sender has no bridge.
- **Why:** A6 rested on "comms' delegated stop leaves queued runs alone, so the seed survives the stop". SW3b showed
  that is false. A defined agent's stop is delegated, the terminal-close path then fails the queued run
  (`terminal_closed`, docs/evidence/2026-10-21-sw3c-stop-attribution), and nothing delivers it after the restart. The
  operator chose to keep that behaviour: "it should get failed status or whatever we add to messages that do not go
  through, but visible in inbox". They then confirmed this restatement on 2026-10-22: "marked FAILED and isn't
  delivered after a restart. It stays in that agent's inbox, and its sender gets a stored 'not delivered' notice. That's
  how comms works today; only the switch test changes", answering "Yes, failed + inbox".
