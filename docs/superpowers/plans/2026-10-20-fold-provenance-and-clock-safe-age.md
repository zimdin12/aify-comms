# Fold-provenance slice and clock-safe turn age: design draft (rev1, 2026-10-20)

**Today.** The fold holds four kinds of knowledge with no build attached: the per-agent turn, each lineage's last clock, each start estimate, and each lineage's freshness (`delivery_turns.py:163-167`). Every fold owner passes `fold=True` (`comms_view.py:249`, `busy_consumers.py:142,164,185,195`). `Provenance.observe` adds the owner to `_fold` (`shadow_provenance.py:112-113`). `add_provenance` adds it to `fold_unverified` (`shadow_evidence_store.py:83`), and `coverage` then always gives that owner `'fold-context'` (`:141`). The start estimate takes a sample from any `ageMs` (`delivery_turns.py:283`). aify-env computes that age as `Date.now() - startedAtUs/1000` when the body is built (`agent-state-publisher.mjs:75-78,107-112`). `startedAtUs` is the hook's own `firedAtUs` (`agent-turn-events.mjs:50`, `turn-events.mjs:21`). The receiver refuses any unknown field (`agent_state_shadow.py:40`), and `ageMs` is its only optional turn field (`:115-117`).

## (a) Each fold entry records the builds behind it

- **What is stored.** Three maps sit beside the existing ones, keyed the same way: `_src_turn`, `_src_lin` and `_src_fresh`, plus `_src_est` for estimates (`delivery_turns.py:163-167`). Each value is a frozenset of builds. A publication with no build counts as `UNKNOWN_BUILD`. The build used is `projection.build`, the same value `Provenance.applied` records (`agent_state_shadow.py:224-225`, `partial_status_shadow.py:83`). A missing entry has an empty set.
- **The write rule.** A write's set is the union of the sets of every fold entry the write read, plus the build of the publication being applied. There are three exceptions:
  - A write that produces `NOTHING_KNOWN`, or an estimate that is not authoritative, gets the empty set. Such a value carries no knowledge.
  - A lineage write gets only `{b}`, the build of this publication (`:266`). It holds just this publication's clock.
  - Reporter writes add no env build (`:221-225`), but they keep the turn's previous set: `reporter_busy` reads `turn.open` (`:109`).
- **What that means per operation:**
  - `ended` → CLOSED gets the previous lineage set plus `{b}`. It does not keep the old turn's set, so knowledge from an older build drops out when a turn ends.
  - `quiet`, `advanced`, `started` and `first` get the old turn set, plus the previous lineage set, plus `{b}`. "Still open" depended on both observations.
  - `ambiguous-closed` → NOTHING_KNOWN gets the empty set.
  - The freshness entry gets its previous set plus `{b}`. Its NOT_KNOWN branch gets `{b}` (`:141-142`).
  - An estimate starts with the previous lineage set, because its authority comes from `previous.open is False` (`:282`). Each sample then adds `{b}`. An estimate's set never resets, since an estimate is history by nature.
  - `_withhold` makes the turn NOTHING_KNOWN with the empty set, removes the freshness entry, and the non-authoritative estimate counts as empty (`:255-261`).
  - `lose_everything` clears every set together with the knowledge (`:246`). Old minimums stay non-authoritative (`:247`), so they count as empty.
- **What a read contributes.** A read adds an entry's set only when the returned value has at least one known field. Every owner combines inputs with three-valued logic: `holds_delivery` (`comms_view.py:136-147`), `_any` and `_all` (`busy_consumers.py:33-44`), and `stranded_candidate` (`:107-113`). Under that logic a NOT_KNOWN input can never decide a known answer. So reading an unknown value needs no provenance. This covers answers and disagreements. The histogram of unknown reasons stays with D-UNKNOWN. The `_origin` and `_reported` diagnostics are excluded, because nothing decides from them (`delivery_turns.py:25-27`).
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
- **The gap stays in these cases:**
  - The stored fold record is missing or malformed: every fold owner is refused, as today (`:73-76`, `store:77-83`).
  - Windows from before this slice already list every reader, so they never lift retroactively. Expired windows are read the same way (`store:218-221`).
  - A publication has an unknown build.
  - A batch reads fold knowledge from a different build.
  - The fold is in the sticky fault.
- **Loss.** An unreadable publication gives `lose_everything` and also `applied(None)`, which adds UNKNOWN to that window's builds (`agent_state_shadow.py:224-225`, `shadow_provenance.py:118`). That window gets the `env-version` gap. Later windows can lift, because the fold kept nothing.
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
  - The 1e-3 margin absorbs NTP frequency slew, so the value is a lower bound on real elapsed time. ASSUMED: slew ≤ 500 ppm, and QPC on Windows is not slewed.
  - Suspend time that the monotonic clock does not count only lowers the age, which makes the estimate later, the safe direction.
  - The age is taken at read time, before the body is built (`agent-state-sender.mjs:147` vs `:174-176`). That is also smaller, so also safe.
- **Publisher changes.** Delete `turnAgeMs` and the wall clock in `#body` (`agent-state-publisher.mjs:74-79,107-113`). Pass `appliedAgeMs` through. Add `appliedAgeMs` to `VOLATILE` (`:38`), otherwise `changes()` would republish every open turn on every tick.
- **What comms may treat as authoritative.** A sample is `(stamp + 1 s) - appliedAgeMs/1000`. `ageMs` alone counts as ageless: non-authoritative for good, never a fallback.
  - `strict_expiry` is known only for a turn the fold saw begin after a closed observation. Every consumed publication of that turn must have carried `appliedAgeMs`, none may have been withheld, and there must have been no loss since (`delivery_turns.py:19-23,278-288`, unchanged otherwise).
  - What this guarantees: the estimate is at or after aify-env's application of the start, and so at or after the true start. No wall clock is compared across processes.
  - Any aify-env restart makes that turn's estimate non-authoritative for good.
  - N2's bound becomes 1800 s + hook→env delay + the smallest env→comms delay + at most 1 s. It is still measured in C8, and N2's text (`P0.md:341-353`) needs amending.
  - ASSUMED, unchanged from today's anchor: comms' own clock is continuous between the estimate and the check.

## Wire and schema changes, in order

1. **C1 (comms):**
   - `_object(turn, …, ('ageMs', 'appliedAgeMs'))` with `_integer` (`agent_state_shadow.py:115-117`).
   - `prepare` reads `appliedAgeMs` into `Record.applied_age_ms` (`partial_status_shadow.py:39,87`). `publish_applied` maps that field (`delivery_turns.py:317`).
   - `ageMs` stays accepted, stays in the raw evidence, and is never read for authority.
   - Fold sets, `Frame.fold_builds`, the Provenance map and the `add_provenance` rule.
   - No DB migration.
   - Update the docstrings at `shadow_provenance.py:13-16` and P0 N2.
2. **Deploy C1, then E1 (aify-env):** the host anchors and the publisher change. If E1 shipped first, comms would refuse every body that has an anchored open turn. A sibling run with `AIFY_ENV_REPO` set to E1 must pass against C1 before E1 deploys. aify-wrapper holds no copy of the schema (plan `:149-150`).

Between the two deploys, every estimate is non-authoritative, so `strict-expiry` answers unknown more often. This is disclosed, and only affects the shadow.

## Test histories (each must fail under the named mutation arm)

- **H1 one-build:** B opens, advances and ends a turn. busy, queue, claim, reminder and stranded are each evaluated. The window is {B} and closes `rotation`. No `fold-context` gap. The control at HEAD shows the gap.
- **H2 upgrade mid-turn:** B_old opens T. B_new republishes T quiet. A read in the {B_new} window is refused. T ends under B_new, and the next window lifts.
  - Arm M1: `ended` drops the previous lineage set.
  - Arm M2: `quiet` adds no set.
- **H3 withheld:** two holders give empty reads, which lift. The withheld estimate is not authoritative. Arm M3: reads add the set of NOT_KNOWN values. This is the false-refusal arm.
- **H4 loss:** `publish_applied(None)` gives an `env-version` gap in that window and a lift in the next.
- **H5 unknown build:** reads carry UNKNOWN, giving `fold-context`. Arm M4: drop the UNKNOWN check.
- **H6 recording fault:** inject a failing set write. The fold stays INDETERMINATE until restart, and its values equal a twin's after every step (the `test_d_unknown` pattern). Arm M5: the next good write clears the fault.
- **H7 estimate across builds:** samples from B_old and B_new; a stale holder's known expiry is refused. Arm M6: the reader omits the estimate's set.
- **H8 reminder freshness:** `advanced_at` from B_old is refused. Arm M7: the reader omits the freshness set.
- **H9 legacy and exception:** `fold=True` or `shadow-error` is refused. Arm M8: INDETERMINATE is treated as empty.
- **H10 old window and malformed record:** both are refused, unchanged.
- **E-step (aify-env):** step `Date.now` +10 s between apply and read. `appliedAgeMs` follows only the monotonic clock. This is the plan's example of a 100.25 s start being read as 90.35 s (`s2c:167`). Arm Mb1: the age uses `Date.now`.
- **E-restart:** an open turn after `boot()` or a new residents host has no age. Arm Mb2: the anchor is set at boot.
- **E-restart-start:** a turn-start on an open turn keeps its anchor. Arm Mb3: every turn-start resets it.
- **E-margin:** 1,000,000 ms elapsed is published as 999,000. Arm Mb4: no margin.
- **E-volatile:** two `changes()` calls that differ only by age return null. Arm Mb5: `appliedAgeMs` is not in VOLATILE.
- **C-wall:** a body with only `ageMs` gives an expiry of NOT_KNOWN. Arm Mc1: fall back to `ageMs`.
- **C-recv:** `appliedAgeMs` is accepted. Negative, float, null and string are refused. Old bodies still pass.

The existing estimate tests that feed `age_ms` move to `appliedAgeMs`: `test_delivery_turns.py:145-211`, `test_shadow_b6a.py`, `test_agent_state_receiver.py`, and in aify-env `agent-state-publisher.test.js`.

## Residuals (not closed here)

- Reporter-sourced knowledge carries no wrapper build. `Versions.wrapper` is never set (`main.py:412`). This slice establishes env-build purity only.
- B5 and the 6c interim restart rule are unchanged.

### Critical files for implementation
- C:\Docker\aify-comms\service\api_core\delivery_turns.py
- C:\Docker\aify-comms\service\api_core\comms_view.py
- C:\Docker\aify-comms\service\api_core\shadow_evidence_store.py
- C:\Docker\aify-comms\service\api_core\agent_state_shadow.py
- C:\Users\Administrator\projects\aify-env\lib\agent-state-host.mjs
