# Decision 2: aify-env announces its report interval, comms derives "not reporting"

Steven's ruling (DECISIONS.md, "aify-env announces its reporting interval", 01M5D9WR): the silence limit is
aify-env's setting. This slice carries it. It comes before SW3b, whose paused case reads the limit.

## What exists (read at env ccf9a7e, comms d2f246ad)

- aify-env reports in full once per `SNAPSHOT_MS` (60 s, `agent-state-sender.mjs`). Between snapshots it posts only
  changes, and a quiet host posts nothing. The daemon's evaluation timer is a second `60_000` literal
  (`setInterval(evaluateAgentState, 60_000)` in `bin/aify-env.mjs`). With timer drift, two ticks can land in one snapshot
  epoch, so a quiet host's worst gap between full reports is about two intervals. That is read from the code, not
  measured. The residents publisher uses the same
  sender, so whatever this slice adds to the sender reaches its bodies too.
- comms ages every publisher with `partial_status_shadow.age_class`: a hardcoded `seconds < 180`, measured from
  `data_applied_at` (comms' receipt of the last data body). It has five readers:
  - `lifecycle_delegation._fresh_publishers` (live today: start, stop and restart delegation);
  - `comms_view.observe_busy` (shadow diagnostic);
  - `partial_status_shadow` (shadow and doctor);
  - `view_owners`;
  - `operational_view.holders_of` (SW2, unwired).
- `agent_state_shadow._validate` refuses unknown top-level fields. A body carrying a new field before comms accepts
  it is refused with 422, and the publisher goes blind.
- Schema copies, searched by `incarnationId`:
  - aify-env's publisher and its tests;
  - comms' validator, its receiver tests, the real-publisher wire fixture `g6_agent_state_wire.mjs`, and the bridge
    doctor test.
  - aify-wrapper has no copy: zero hits, against a control of 14 hits for `turnStart` in the same repo.

## The change

1. **Field.** `reportEveryMs`, an integer of milliseconds, top level, on every body except `retired` (snapshot,
   changes, unavailable), beside `build` and placed the same way. Bounds: 1,000 to 3,600,000.
   - aify-env omits a value outside the bounds, as it does `build`, and never sends null.
   - comms refuses a body whose value is present and malformed (wrong type, float, bool, out of bounds), as
     `_build` does. An absent field is accepted, so today's aify-env keeps validating.
2. **One owner in aify-env.** The sender exports the interval. The publisher stamps it, and the daemon's timer takes
   it, so the second `60_000` literal is gone.
3. **Reporting contact is kept apart from data.** comms keeps a process-local pair per publisher in the mirror:
   `Publisher.report = (received_at, report_every_ms)`.
   - **When it is written:** only when the ingest *applies* an advancing, non-retired publication: a snapshot, a
     change or an unavailable report. `received_at` is that receipt on comms' clock, and `report_every_ms` is that
     same body's field.
   - **When it is left alone:** a duplicate, stale, conflicting, refused or retired body changes nothing.
   - **A newly accepted body without the field** sets the interval to none, which withdraws the announcement with
     no fallback.
   - **What stays DATA-only, exactly as today:**
     - `data_applied_at`, the rows and the build origin;
     - an unavailable report never freshens data, and never clears the restored, incomplete, loss or unavailable
       guards on eligibility.
   - **Not persisted:** a restored publisher has no report pair, so it reads `not-reporting`. It recovers its pair
     on its next accepted report, and its eligibility only from a complete snapshot from its own key (SW1,
     unchanged).
4. **The derived limit.** A new pure function, `report_age(publisher, at)`, reads only that pair:
   - `fresh` while `at - received_at < 3 x report_every_ms`;
   - `stale` from that point;
   - `not-reporting` when the pair is absent or its interval is none: no guessed number, and never fresh;
   - `invalid-clock` and `future` exactly as `age_class` returns them.
   `report_age` says whether aify-env is in contact. Whether its rows can be used is still decided by data age and
   the eligibility guards.
   Three is the protocol policy ("three missed reports") and lives once, in this function. The rationale that
   three intervals cover a two-interval quiet gap is nominal, read from the code, not a measured bound.

## Transition: nothing live degrades before the switch

The running aify-env announces nothing until Steven's env restart onto the new build. So:

- **Changes now:** `operational_view.holders_of` reads `report_age`, and `not-reporting` maps to unknown. It is
  unwired (SW2), so no live answer moves.
- **Unchanged until SW5:** `lifecycle_delegation`, `comms_view`, `partial_status_shadow`'s own reader and
  `view_owners`. They keep `age_class`, which is renamed `legacy_receipt_age` and holds the 180 as the named
  constant `LEGACY_RECEIPT_LIMIT_S`. SW5 moves each one to `report_age` in its single revertible commit, and SW7
  deletes the legacy function and constant.
- **Deploy order:**
  - comms first, accepting and recording the field;
  - then aify-env main, live at Steven's next env restart.
  - aify-env must not merge until the comms build that accepts the field is deployed. Otherwise every body is
    refused.
  - SW3b's pinned pair is that comms and that aify-env.

## Witnesses (each watched RED on the pre-change tree or by a named arm)

| witness | RED by |
|---|---|
| aify-env: every snapshot, changes and unavailable body carries `reportEveryMs` equal to the sender's exported interval; retired carries none | today's publisher (absent) |
| aify-env: the daemon timer and the snapshot epoch read the one exported constant | arm: the timer literal restored |
| comms: an absent field is accepted (control); 0, -1, 1.5, "60000", true, null and 3,600,001 are each refused, with the database unchanged | arm: the check removed |
| comms: interval 60,000 is fresh at 179.9 s and stale at 180 s; interval 20,000 is stale at 60 s | arm: the 180 hardcoded (the second case fails) |
| comms: no announcement reads `not-reporting` at 1 s, never fresh | arm: a 180 s fallback |
| comms: valid unavailable reports every 60 s for 10 min read report-fresh, while the publisher stays operationally unknown and `data_applied_at` is unchanged | arm: `report_age` reading `data_applied_at` (goes stale); arm: unavailable writing `data_applied_at` |
| comms: a duplicate, a stale, a conflicting and a refused (422) body each leave the pair unchanged; a retired body removes the publisher | arm: the pair written before the ordering verdict |
| comms: an accepted body without the field, after one with it, reads `not-reporting` | arm: the previous interval kept |
| comms: a restored key reads `not-reporting` and unknown; an unavailable report restores the pair but not eligibility; only a complete snapshot from its own key restores eligibility | arm: unavailable clearing `restored` |
| comms: `lifecycle_delegation` still delegates to a fresh publisher that announced nothing (the transition control) | arm: `age_class` switched to `report_age` |
| comms: `operational_view` reads unknown for a holder that announced nothing | today's holders_of (fresh) |
| cross-repo: the real aify-env publisher's body (via `g6_agent_state_wire.mjs`, run with `AIFY_ENV_REPO` set to the candidate) validates in comms with the field present | the pre-change comms validator (refuses) |

## Not in this slice

SW5's rewiring of the four legacy readers; deleting `legacy_receipt_age` (SW7); any change to the turn law or to
what `busy` means.
