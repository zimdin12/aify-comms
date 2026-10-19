# B6c: the stale-turn ceiling owner (design draft, rev1)

**1. Today and the candidate.** Today has one ceiling law: `turn_is_still_live` with `renewable=False` and `strict_seconds=TURN_BUSY_BACKSTOP_SECONDS=1800` (turn_liveness_policy.py:28-74, liveness.py:171). It holds while `0 <= now - (started or touched) <= 1800` (:70-74). The anchor is stamped when the service receives the busy transition, then preserved: `agent_turn_state.turn_started_at` (turn_busy_signal.py:76-82, turn_boundaries.py:135-139) and `agent_status_state.turn_started_at` (status_events.py:45-51). Three readers apply the law:
- delivery, `turn_busy_decision` (claim_gating.py:460-480);
- the status clamp `_in_turn_survives_the_ceiling` (status_inputs.py:115-134), called at :172 for :226 and :674;
- the legacy status read (turn_state.py:216-231).

C8's "today" is the strict arm of the delivery reader (P0:388, s2c:79), because C6's stale disjunct is strict and never renewed (P0:317). The delivery reader never computes that arm alone when the renewal is verified (claim_gating.py:474-480). So B6c recomputes it with the same pure function from what today read: `BusyDecision.receipt` holds `turn_started_at`, `turn_updated_at` and `nowEpoch` (claim_gating.py:352-363, :481-482). A parity control pins that this equals `decision.holds` whenever the renewal is not `verified`. The candidate is `nowEpoch < FOLD.strict_expiry(machine, agent, lifetime, startedAtUs)`, which is the earliest authoritative estimate plus 1800 (delivery_turns.py:209-213, :278-288). That is the same comparison `_record_disjunct` makes (comms_view.py:116-118).

**2. Recording.** The owner is `stale-turn ceiling`, variant `-` (shadow_owners.py:15, :19-29). There are two sites, `send-time` and `claim`, with one new observer `observe_ceiling(decision, busy, site)`:
- **Where it runs:** called right after `observe_busy` (dispatch_launch.py:90, dispatch_claim.py:369).
- **What it reads:** only busy's `Basis` (comms_view.py:167-174). It does no await, no SQL and no second bind, so it adds **0 queries** to the claim path.
- **Today:** the recomputed strict arm.
- **Candidate:** for the one open holder in the frame, fresh or stale (comms_view.py:88-92). Holder freshness is kept in evidence.
- **One instant:** both sides are evaluated at the receipt's `nowEpoch`, so a disagreement is purely an anchor difference. `frame.at` is bound later (comms_view.py:70-71) and is only recorded. Evaluating the candidate at `frame.at` is mutation arm M5.
- **Denominator:** an evaluation happens only where today applies a ceiling, meaning the receipt has `read=='row'` and `turn_busy=1`. A not-busy row records nothing, because today made no ceiling decision there. This is disclosed.

The candidate is unknown, reason in this order, and is never counted as False:
- `comms-receipt`: the receipt is None;
- `record-knowledge`: a lost binding (comms_view.py:73-74), an unknown-freshness holder, or `others_unknown`;
- `ambiguous-association`: more than one holder;
- `unpaired`: no holder, or its turn is closed or null while today's row is busy. A known closed record turn is still not a ceiling anchor;
- `strict-expiry`: `strict_expiry` is NOT_KNOWN. That covers non-authoritative (seeded, withheld, lost, ageless) and absent estimates (delivery_turns.py:211-212, :244-247, :255-261, :284-285);
- `shadow-error`: any raise, through `cv.record_answer`'s containment (comms_view.py:221-233).

`Recorder.record_owner` already turns `candidate=None` plus a reason into `unavailable:<reason>` (partial_status_shadow.py:320-336, shadow_owners.py:56-68).

Two known systematic classes go to the operator, not into code:
- **Boundary:** today is inclusive (`<= 1800`) and the candidate is exclusive (`<`).
- **Resolution:** the candidate adds 1 s for stamp resolution (delivery_turns.py:283). Today's anchor is a truncated `now()` (clock.py:21-30), ASSUMED for the heartbeat path, whose caller I did not read. So the candidate runs up to about 1 s later.

**Not instrumented in B6c:** the status clamp's anchor (`agent_status_state`, status_inputs.py:115-134) and turn_state.py:216-231. Both would need a bind on the batch status path. They are disclosed like promotion's word paths (s2c:105-109) and need their own slice before C8.

**3. Evidence, not counts-only.** Reminder and stranded are counts-only because a wrong answer there costs one nudge (s2c:146-148, shadow_evidence.py:27-30). The ceiling is different on three counts:
- It decides how long a stale record **holds messages** (P0:317).
- C8 and N2 require "both anchors recorded per turn", and "a turn that holds longer under the new one is a classified difference" (P0:350-353, :388). Counts cannot carry anchors, so they cannot classify a disagreement.
- It costs no new reads. The item is `cv.basis_item(...)` (comms_view.py:204-210), which already carries the receipt, the holders with `clock` and `expiry`, and the raw census (shadow_evidence.py:93-117). The ceiling adds only `todayAnchor`, the anchor's source column (or the `turn_updated_at` fallback, turn_liveness_policy.py:70), `todayExpiry`, `candidateExpiry`, holder freshness, `frameAt` and the reason.

To implement it, add the owner to `EVIDENCE_OWNERS` (shadow_evidence.py:29). Only windows opened after that carry it as evidence; earlier windows stay counts-only for it (shadow_windows.py:136, s2c:143-145).

**4. Open obligations.**
- **Fold provenance (option iii):** the candidate reads the fold, so the owner is recorded with `fold=True` and added to `FOLD_OWNERS` (shadow_provenance.py:27, :73-76). Every window then gives it the `fold-context` gap, so it is never complete (shadow_evidence_store.py:141-150). B6c measures; it does not qualify, and it does not lift that gap.
- **Clock-safe age:** a forward producer clock step inflates `ageMs`. The kept minimum makes one bad sample permanent (delivery_turns.py:287), so the candidate expires early and shows as `disagree:true:false`. That is a delivery the hold should have stopped. A backward step can move a first estimate later and hold longer. B6c can surface both directions with both anchors attached, but it cannot separate a step from a real delay: the fold keeps no per-sample history. Closing that stays with the fold-provenance slice (s2c:165-171, P0:341-346).

**5. Tests** (new `service/tests/test_stale_turn_ceiling.py`, built on `test_delivery_turns.py` and `test_shadow_evidence_e2.py` fixtures). Named histories:
- `begun-within-both` agrees true; `begun-past-both` agrees false.
- `late-receipt`: today's anchor is 20 s after the estimate, giving `disagree:true:false` and one item holding both anchors.
- `resolution-edge` and `exact-boundary` pin the two systematic classes.
- `seeded-turn`, `withheld-then-consumed`, `ageless-sample` and `lost-everything` each count `unavailable:strict-expiry`, never false.
- `forward-step-10s`: the plan's 100.25 s start becoming 90.35 s. The disagreement is recorded, and the owner shows `fold-context`.
- `two-open-holders`, `record-closed-today-busy` and `lost-binding` give their three reasons.
- `today-not-busy` records nothing.
- `anchorless-row` shows the evidence naming the fallback column.
- `verified-renewal-past-ceiling`: today's strict arm is false while delivery holds.
- `parity`: the recomputation equals `decision.holds` whenever the renewal is not verified.
- `isolation`: a forced raise leaves the claim answer, the commit and the queue identical, and counts `shadow-error`.
- `claim-query-count`: unchanged.

Mutation arms, each of which must fail a test above:
- **M1** NOT_KNOWN expiry read as False
- **M2** `strict_expiry` ignores `authoritative`
- **M3** today's arm takes the verified renewal
- **M4** `fold=False` or the owner missing from `FOLD_OWNERS`
- **M5** candidate evaluated at `frame.at`
- **M6** `<` becomes `<=`
- **M7** the first holder taken under a conflict
- **M8** a not-busy row recorded as today False
- **M9** owner missing from `EVIDENCE_OWNERS`
- **M10** the observer re-reads `agent_turn_state`

ASSUMED: the env record has no run id (P0:17), so pairing "both open in one evaluation" can join a latched old comms turn to a new env turn. That shows up as an early today anchor in the evidence, and is classified there rather than filtered in code.

### Critical Files for Implementation
- C:\Docker\aify-comms\service\api_core\comms_view.py
- C:\Docker\aify-comms\service\api_core\claim_gating.py
- C:\Docker\aify-comms\service\api_core\delivery_turns.py
- C:\Docker\aify-comms\service\api_core\shadow_provenance.py
- C:\Docker\aify-comms\service\api_core\shadow_evidence.py
