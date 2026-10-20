# Fold-provenance slice and clock-safe turn age: design draft (rev3, 2026-10-20; answers REVISE AGE-1 and FOLD-1, and the rev2 review's window boundary and C1 parent)

**Today.** The fold holds four kinds of knowledge with no build attached: the per-agent turn, each lineage's last clock, each start estimate, and each lineage's freshness (`delivery_turns.py:163-167`). Every fold owner passes `fold=True` (`comms_view.py:249`, `busy_consumers.py:142,164,185,195`). `Provenance.observe` adds the owner to `_fold` (`shadow_provenance.py:112-113`). `add_provenance` adds it to `fold_unverified` (`shadow_evidence_store.py:83`), and `coverage` then always gives that owner `'fold-context'` (`:141`). The start estimate takes a sample from any `ageMs` (`delivery_turns.py:283`). aify-env computes that age as `Date.now() - startedAtUs/1000` when the body is built (`agent-state-publisher.mjs:75-78,107-112`). `startedAtUs` is the hook's own `firedAtUs` (`agent-turn-events.mjs:50`, `turn-events.mjs:21`). The receiver refuses any unknown field (`agent_state_shadow.py:40`), and `ageMs` is its only optional turn field (`:115-117`).

## (a) Each fold entry records the builds behind it

- **What is stored.** Three maps sit beside the existing ones, keyed the same way: `_src_turn`, `_src_lin` and `_src_fresh`, plus `_src_est` for estimates (`delivery_turns.py:163-167`). Each value is a frozenset of builds. A publication with no build counts as `UNKNOWN_BUILD`. The build used is `projection.build`, the same value `Provenance.applied` records (`agent_state_shadow.py:224-225`, `partial_status_shadow.py:83`). A missing entry has an empty set.
- **The write rule.** A write's set is the union of the sets of every fold entry the write read, plus the build of the publication being applied. There are three exceptions:
  - A write that produces `NOTHING_KNOWN`, or an estimate that is not authoritative, gets the empty set. Such a value carries no knowledge.
  - A lineage write gets only `{b}`, the build of this publication (`:266`). It holds just this publication's clock.
  - Reporter writes add no env build (`:221-225`), but they keep the turn's previous set: `reporter_busy` reads `turn.open` (`:109`).
- **[FOLD-1] The previous set is frozen first.** `_consume` takes `previous` and then overwrites the lineage (`delivery_turns.py:265-266`). After that it writes the freshness entry, the estimate and the turn (`:267,273,276`). The lineage's previous set is read in the same step as `previous`, before `_src_lin` becomes `{b}`. Every set that this consume writes uses that frozen value and never the overwritten one.
- **What that means per operation:**
  - `ended` → CLOSED gets the previous lineage set plus `{b}`. It does not keep the old turn's set, so knowledge from an older build drops out when a turn ends.
  - `quiet`, `advanced`, `started` and `first` get the old turn set, plus the previous lineage set, plus `{b}`. "Still open" depended on both observations.
  - `ambiguous-closed` → NOTHING_KNOWN gets the empty set.
  - The freshness entry gets its previous set plus `{b}`. Its NOT_KNOWN branch gets `{b}` (`:141-142`).
  - An estimate starts with the previous lineage set, because its authority comes from `previous.open is False` (`:282`). Each sample then adds `{b}`. An estimate's set never resets, since an estimate is history by nature.
  - `_withhold` makes the turn NOTHING_KNOWN with the empty set and removes the freshness entry. The non-authoritative estimate counts as empty (`:255-261`).
  - `lose_everything` clears every set together with the knowledge (`:246`). Old minimums stay non-authoritative (`:247`), so they count as empty.
- **What a read contributes.** A read adds an entry's set only when the returned value has at least one known field. Every owner combines inputs with three-valued logic: `holds_delivery` (`comms_view.py:136-147`), `_any` and `_all` (`busy_consumers.py:33-44`), and `stranded_candidate` (`:107-113`). Under that logic a NOT_KNOWN input can never decide a known answer, so reading an unknown value needs no provenance. This covers answers and disagreements. The histogram of unknown reasons stays with D-UNKNOWN. The `_origin` and `_reported` diagnostics are excluded, because nothing decides from them (`delivery_turns.py:25-27`).
- **If recording fails.** Any exception while writing a set makes the whole fold sticky-INDETERMINATE until comms restarts. This follows the `_diagnostics` pattern (`:170,175-181`). Semantic values are never touched.
- **Where reads collect their builds.** `capture()` gets `Frame.fold_builds`. It is computed from the same keys it reads: the turn (`comms_view.py:72`) and each open holder's `strict_expiry` and `advanced_at` (`:90-91`). It is computed in the same synchronous call, before the one await (`:13-14`). `observe_stranded` computes its set in the same expression as its synchronous `FOLD.turn` read (`busy_consumers.py:192`).

## The window rule: when `fold-context` lifts

- **What callers pass.** `record_answer(fold=…)` now takes one of three values:
  - `False`: the fold was not read. Reminder's `blocked` and `occupied` paths and the association failure (`busy_consumers.py:168-177`) become `False`.
  - A frozenset of builds: the fold was read, with these builds behind it.
  - INDETERMINATE: legacy `True`, any exception path, or a fold in the sticky fault.
- **What Provenance keeps.** It keeps `{owner: union of sets}` and the set of INDETERMINATE owners. `take()` returns both (`shadow_provenance.py:107-130`).
- **The lifting rule (in `add_provenance`).** Let `window = seen | builds`. This is the window's builds after this batch's append. A new build closes the window only after the append (`shadow_windows.py:113-117`), so this is the window's final set. An owner is added to the stored `fold_unverified` if any of these holds:
  1. it was INDETERMINATE in this batch, or
  2. `UNKNOWN_BUILD` is among its builds, or
  3. its builds are not a subset of `window`.

  The column, its parsing, `fold_unverified` (`shadow_provenance.py:73-76`), `coverage` and tombstones are unchanged. An owner lifts only if it is also free of the `env-version` gap (exactly one known build, `:61-64`), so its fold builds ⊆ {B}. It also still needs a clean close and a partition that holds (`shadow_evidence_store.py:149-150`).
- **[FOLD-1] Rule 2 changes attribution, never completeness.** Suppose rule 2 is the only fold rule that fires. Then UNKNOWN is in `window`, so `env_coherent` is false (`shadow_provenance.py:64`) and the owner is already incomplete. Rule 2 exists so that `gaps` names `fold-context` (`shadow_evidence_store.py:141`). Its test (H5) is therefore an attribution test.
- **The gap stays in these cases:**
  - The stored fold record is missing or malformed: every fold owner is refused, as today (`:73-76`, `store:77-83`).
  - Windows from before this slice already list every reader, so they never lift retroactively. Expired windows are read the same way (`store:218-221`).
  - A publication has an unknown build.
  - A batch reads fold knowledge from a different build.
  - The fold is in the sticky fault.
- **Loss.** An unreadable publication causes `lose_everything` and also `applied(None)`, which adds UNKNOWN to that window's builds (`agent_state_shadow.py:224-225`, `shadow_provenance.py:118`). That window gets the `env-version` gap. Later windows can lift, because the fold kept nothing.
- **Withheld.** A withhold reads as empty and does not block lifting.
- **Missing or unreadable record.** A holder marked unknown (`comms_view.py:84-87`) reads no expiry or freshness entry. Only the turn's set counts.
- **comms restart.** The window closes `service-restart`, which is never complete. The fold starts empty, so the next window can lift.
- **aify-env restart on the same build.** Fold knowledge continues as `{B}`. The ages disappear (see (b)), so estimates become non-authoritative.
- **aify-env restart on a new build.** Entries from before the restart keep the old build until they are rewritten: a lineage on its next publication, a turn when it ends or is withheld, an estimate never. So an open turn that crossed the upgrade keeps its owners refused until it ends, or until the 6c interim comms restart.
- **What lifting does not do.** It does not close B5 (6c) or D-UNKNOWN (6b).

## (b) A clock-safe age: `turn.appliedAgeMs`

- **What aify-env measures.** `AgentStateHost` gets an injectable `monoMs` (default `performance.now`). It keeps `#anchors: lifetime → {startedAtUs, atMono}`.
  - An anchor is set in `applyEvent` only after the durable write succeeds (`agent-state-host.mjs:129-133`). It is set only when the applied event opens a new `startedAtUs`. A turn-start on a turn that is already open keeps its start (`turn-events.mjs:21`), and so keeps its anchor. `blocked` and `unblocked` keep it too.
  - turn-end and `endManaged` delete the anchor.
  - `boot()` sets no anchor (`agent-state-host.mjs:84-86`). A residents host built again when the role is re-acquired has none either (`daemon-residents.mjs:87-89`).
- **What it publishes.** `current()` emits `turn.appliedAgeMs = floor((monoMs() - atMono) × (1 - 1e-3))`, but only for an open turn whose anchor's `startedAtUs` equals the turn's. Otherwise the field is omitted (`:165-169`).
  - The 1e-3 margin absorbs NTP frequency slew, so the value is a lower bound on real elapsed time. ASSUMED: slew ≤ 500 ppm, and QPC on Windows is not slewed. Not validated by review.
  - Suspend time that the monotonic clock does not count only lowers the age. That makes the estimate later, the safe direction.
  - The age is taken at read time, before the body is built (`agent-state-sender.mjs:147` vs `:174-176`). That is also smaller, so also safe.
- **Publisher changes.** Delete `turnAgeMs` and the wall clock in `#body` (`agent-state-publisher.mjs:74-79,107-113`). Pass `appliedAgeMs` through. Add `appliedAgeMs` to `VOLATILE` (`:38`); otherwise `changes()` would republish every open turn on every tick.
- **What comms may treat as authoritative.** A sample is `(stamp + 1 s) - appliedAgeMs/1000`. `ageMs` alone counts as ageless: non-authoritative for good, never a fallback.
  - `strict_expiry` is known only for a turn the fold saw begin after a closed observation. Every consumed publication of that turn must have carried `appliedAgeMs`, none may have been withheld, and there must have been no loss since (`delivery_turns.py:19-23,278-288`, unchanged otherwise).
  - What this guarantees: the estimate is at or after aify-env's application of the start, and so at or after the true start. No wall clock is compared across processes.
  - Any aify-env restart makes that turn's estimate non-authoritative for good.
  - ASSUMED, unchanged from today's anchor: comms' own clock is continuous between the estimate and the check.
- **[AGE-1] What the lateness is made of (replaces rev1's N2 bound).** Measure on comms' clock, and take each consumed sample i:
  - `sample_i − start = h + d_i + u_i + p_i`, where:
    - h is the delay from the hook to aify-env's application;
    - d_i is the delay from env's read (`agent-state-sender.mjs:147`) to comms' application;
    - p_i = stamp_i + 1 − application, which lies in (0, 1] s, because the stamp truncates to whole seconds (`clock.py:21-30`, `delivery_turns.py:283`);
    - u_i is the undercount: true time since application minus `appliedAgeMs/1000`. It is about 1e-3 × that time, plus under 1 ms of floor, plus any suspend the monotonic clock omitted.
  - The estimate keeps the minimum (`:287`), so `strictExpiry − (start + 1800 s) = h + min_i(d_i + u_i + p_i)`.
  - No early expiry needs only every term ≥ 0. h and d_i are ≥ 0 by causality, p_i > 0, and u_i ≥ 0 under the slew ASSUMPTION above. **This is the only guarantee kept.**
  - **No finite latest bound is claimed (route b).** u_i grows with the sample's age and with omitted suspend, and comms can bound neither:
    - Authority needs only a closed observation before the turn (`:282`). A publication that never arrived is invisible to comms, so the first admitted sample can be read long after the start.
    - Suspend is unobservable.
  - Route (a) is declined, because it would rest on those unchecked terms.
- **[AGE-1] Worked countermodel (E-margin's values, ideal clocks).**
  - Start and env application are both at 0, so h = 0.
  - The first admitted open sample is read and applied at 1000 s, so d = 0. Its stamp is 1000, so p = 1.
  - Monotonic elapsed is 1,000,000 ms, so `appliedAgeMs` = floor(1,000,000 × 0.999) = 999,000 and u = 1 s.
  - Sample = 1000 + 1 − 999 = 2, so expiry = 1802. rev1's limit was 1800 + 0 + 0 + 1 = 1801.
  - Sample selection cannot repair this. Every later sample is read later, so its u is ≥ 1 s and its p is > 0; the kept minimum stays above 1, and expiry stays above 1801.
  - With 600 s of suspend omitted before that read: monotonic elapsed is 400,000 ms, `appliedAgeMs` = 399,600, u = 600.4 s, sample = 601.4, expiry = 2401.4. The excess has no bound as suspend grows.
- **[AGE-1] N2 amendment for P0 (replaces `2026-10-02-aify-env-owns-agent-state-P0.md:341-353`).** This is a contract change. It goes to P0's owner (the operator approved P0's end state, `P0.md:3-5`) and must be accepted before C1 starts. The C8 row at `P0.md:388` is unchanged. **[rev3]** The review accepted the mathematics, not the contract change; the change is routed to the operator as dashboard question `01M5CA55NBE62M25ZYCCQZJR2C` (2026-10-20), and C1 waits on that answer.

  > **The stale anchor (N2)** (amended 2026-10-20). A record's open turn carries `turn.appliedAgeMs` only when the publishing aify-env host applied that turn's start itself. The value is the monotonic time since that application, reduced by 1e-3 and floored. Each consumed publication gives a sample of the start on aify-comms' clock: `(appliedAt stamp + 1 s) - appliedAgeMs`. aify-comms keeps the **earliest** sample per turn (`startedAt` identifies the turn). The sample is authoritative only for a turn the fold saw begin after a closed observation, with every consumed publication of it aged, none withheld, and no loss since. `strictExpiry` is that sample plus 1800 s. `turn.ageMs` is accepted and is never authoritative.
  >
  > The guarantee, named: `strictExpiry` is never earlier than 1800 s after the true start. ASSUMED: aify-env's monotonic clock runs fast by at most 1e-3, and aify-comms' clock is continuous between the sample and the check. **No finite latest bound is claimed.** The excess over 1800 s is h + min over samples of (d + u + p):
  > - h is the hook-to-env application delay;
  > - d is the env-read-to-comms-application delay;
  > - p is the stamp padding, in (0, 1] s;
  > - u is the age undercount: about 1e-3 of the age, plus any suspend the monotonic clock omitted. It is unbounded.
  >
  > Today's anchor holds 1800 s plus the hook's delivery delay. In C8 the difference is measured, not assumed: both anchors are recorded per turn, and a turn that holds longer under the new one is a classified difference. A ceiling never restarts, and an estimate can only move earlier. Any aify-env restart during the turn makes its estimate non-authoritative for good.

## Wire and schema changes, in order

1. **C1 (comms):**
   - `_object(turn, …, ('ageMs', 'appliedAgeMs'))` with `_integer` (`agent_state_shadow.py:115-117`).
   - `prepare` reads `appliedAgeMs` into `Record.applied_age_ms` (`partial_status_shadow.py:39,87`). `publish_applied` maps that field (`delivery_turns.py:317`).
   - `ageMs` stays accepted, stays in the raw evidence, and is never read for authority.
   - Fold sets, `Frame.fold_builds`, the Provenance map and the `add_provenance` rule.
   - No DB migration.
   - **[rev3] C1's parent is main with B6c merged** (`349d61b3` or later). The fold owners are the five H1 owners plus B6c's stale-turn ceiling (`stale_turn_ceiling.py`, wired at `dispatch_launch.py` and `dispatch_claim.py` after `observe_busy`), so the ceiling takes the same `fold=True` path and is in every history below.
   - Update the docstrings at `shadow_provenance.py:13-16` and `delivery_turns.py:19-23`. **[AGE-1]** P0 N2 changes only through the amendment above, and C1 does not start until it is accepted.
2. **Deploy C1, then E1 (aify-env):** the host anchors and the publisher change. If E1 shipped first, comms would refuse every body that has an anchored open turn. A sibling run with `AIFY_ENV_REPO` set to E1 must pass against C1 before E1 deploys. aify-wrapper holds no copy of the schema (plan `:149-150`).

Between the two deploys, every estimate is non-authoritative, so `strict-expiry` answers unknown more often. This is disclosed, and it only affects the shadow.

## Test histories (each must fail under the named mutation arm)

**[FOLD-1] Rule for refusal assertions.** Every refusal is asserted as the owner's exact `gaps` list (`shadow_evidence_store.py:141`), never as generic incompleteness. H2's reads go through the stranded-reply guard. It reads only `FOLD.turn` (`busy_consumers.py:188-195`) and passes no binding, so it adds no build (`shadow_provenance.py:87-88`). The turn's set alone then decides `fold-context`.

- **H1 one-build:** B opens, advances and ends a turn. busy, queue, claim, reminder, stranded and the stale-turn ceiling are each evaluated. The window is {B} and closes `rotation`. No `fold-context` gap. The control at HEAD shows the gap.
- **H2a cross-build end [FOLD-1]:**
  - Start from a fresh fold. B_old publishes T open (`first`, lineage {B_old}).
  - B_new's first publication carries T closed, with no B_new quiet before it. That is `ended` (`delivery_turns.py:127-128`), so CLOSED carries {B_old, B_new}.
  - That batch's window holds both builds and closes `version-change` (`shadow_windows.py:113-117`). The next window is seeded from the mirror with {B_new} (`shadow_provenance.py:122-129`).
  - A stranded read there gives gaps == ['fold-context']. The window is coherent, so the subset rule alone refuses.
  - Arm M1: `ended` drops the previous lineage set. CLOSED carries {B_new} and gaps == [].
  - Arm M1f: the previous lineage set is read after the overwrite. Same result as M1.
  - Arm M4s: drop rule 3 (subset). gaps == [].
- **H2b lift control [FOLD-1, rev3 window boundary]:** the fold recovers inside a window; the window does not.
  - Continue from H2a, in the {B_new} window W1. B_new opens and then ends T2.
  - A read between the two, in W1, is refused with gaps == ['fold-context'], because `started` leaves the turn as it was (`delivery_turns.py:149-157`) and so keeps its {B_old, B_new} set.
  - After T2's end, CLOSED carries {B_new}: the FOLD has recovered. W1 has not: its per-owner union and persisted `fold_unverified` already hold the earlier refused read, so a read in W1 after the end still gives gaps == ['fold-context']. Nothing resets the fold or erases that read to manufacture a lift.
  - W1 closes `rotation` with its history as written, and the fold is kept across the rotation. In the fresh window W2, seeded {B_new} from the mirror, a stranded read gives gaps == []. That is the lift.
  - Arm M1b: `ended` also keeps the old turn's set. The W2 read still refuses (['fold-context']). This is the false-refusal arm.
  - Arm M1e: T2's end erases W1's earlier refused read (clears the owner's union or `fold_unverified`). W1's post-end read gives gaps == [] where ['fold-context'] is expected.
- **H2d rotation keeps the fold [rev3]:** continue from H2a, but rotate W1 while T2 is still open. In the fresh window W2 a stranded read is still refused (['fold-context']), because the open turn still carries {B_old, B_new}; rotation moves the window, not the fold. After T2 ends, the lift is asserted in the next fresh window W3, as in H2b.
  - Arm M1r: rotation clears the fold sets. The W2 read lifts.
- **H2c quiet before end (rev1's H2, kept for its healthy behavior):**
  - A reporter opens the turn (`delivery_turns.py:107-110`). B_old publishes T open, B_new republishes T quiet, and T ends under B_new.
  - A stranded read in the {B_new} window W1 while T is open gives ['fold-context'].
  - **[rev3 window boundary]** After T ends under B_new, CLOSED carries {B_new}, but a read in W1 still gives ['fold-context']: the window keeps the refused read. W1 closes `rotation` without rewriting it, the fold is kept, and in the fresh {B_new} window W2 the read gives []. The lift is asserted only in W2.
  - Arm M2: a `quiet` write records the empty set. The read while T is open lifts.
- **H3 withheld:** two holders give empty reads, which lift. The withheld estimate is not authoritative. Arm M3: reads add the set of NOT_KNOWN values. This is the false-refusal arm.
- **H4 loss:** `publish_applied(None)` gives an `env-version` gap in that window and a lift in the next. **[FOLD-1]** An owner that read {B} before the loss gets gaps == ['env-version'] exactly: {B} ⊆ window and carries no UNKNOWN, so no fold rule fires.
- **H5 unknown build [FOLD-1, split]:**
  - Use the first window after a start, with an empty mirror (`shadow_windows.py:114-116`). In it, a buildless publication (`shadow_provenance.py:118`) opens and closes a turn, so the window's only build is UNKNOWN.
  - A stranded read carries {UNKNOWN} ⊆ window, so rule 3 is silent.
  - Expected gaps == ['env-version', 'fold-context'].
  - Arm M4: drop rule 2. gaps == ['env-version']. The test asserts `fold-context` by name. `env-version` appears under both arms and is not the asserted refusal.
- **H6 recording fault:** inject a failing set write. The fold stays INDETERMINATE until restart, and its values equal a twin's after every step (the `test_d_unknown` pattern). Arm M5: the next good write clears the fault.
- **H7 estimate across builds:** samples come from B_old and B_new, and a stale holder's known expiry is refused. Arm M6: the reader omits the estimate's set.
- **H8 reminder freshness:** `advanced_at` from B_old is refused. Arm M7: the reader omits the freshness set.
- **H9 legacy and exception:** `fold=True` or `shadow-error` is refused. Arm M8: INDETERMINATE is treated as empty.
- **H10 old window and malformed record:** both are refused, unchanged.
- **E-step (aify-env):** step `Date.now` by +10 s between apply and read. `appliedAgeMs` follows only the monotonic clock. This is the plan's example of a 100.25 s start being read as 90.35 s (`s2c:167`). Arm Mb1: the age uses `Date.now`.
- **E-restart:** an open turn after `boot()` or a new residents host has no age. Arm Mb2: the anchor is set at boot.
- **E-restart-start:** a turn-start on an open turn keeps its anchor. Arm Mb3: every turn-start resets it.
- **E-margin:** 1,000,000 ms elapsed is published as 999,000. Arm Mb4: no margin.
- **E-volatile:** two `changes()` calls that differ only by age return null. Arm Mb5: `appliedAgeMs` is not in VOLATILE.
- **C-wall:** a body with only `ageMs` gives an expiry of NOT_KNOWN. Arm Mc1: fall back to `ageMs`.
- **C-recv:** `appliedAgeMs` is accepted. Negative, float, null and string values are refused. Old bodies still pass.

The existing estimate tests that feed `age_ms` move to `appliedAgeMs`: `test_delivery_turns.py:145-211`, `test_shadow_b6a.py`, `test_agent_state_receiver.py`, and in aify-env `agent-state-publisher.test.js`.

## Residuals (not closed here)

- Reporter-sourced knowledge carries no wrapper build, because `Versions.wrapper` is never set (`main.py:412`). This slice establishes env-build purity only.
- B5 and the 6c interim restart rule are unchanged.
- **[AGE-1]** The latest-expiry excess is unbounded by design and is only measured in C8. The slew assumption is unvalidated.

### Critical files for implementation
- C:\Docker\aify-comms\service\api_core\delivery_turns.py
- C:\Docker\aify-comms\service\api_core\shadow_evidence_store.py
- C:\Docker\aify-comms\service\api_core\shadow_provenance.py
- C:\Docker\aify-comms\docs\superpowers\plans\2026-10-02-aify-env-owns-agent-state-P0.md
- C:\Users\Administrator\projects\aify-env\lib\agent-state-host.mjs
