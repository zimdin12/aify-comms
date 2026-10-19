# B5: retiring a superseded agent-state publisher, with aify-env as the authority (DRAFT, revision 2)

**Where B5 lives in the code.** A managed key is `default` or `herdr-<invocation>` (aify-env `lib/daemon-agent-state.mjs:17-19`). The residents key is `residents-<hash12>` (`lib/residents-home.mjs:72-75`). Successors reuse `default` and `residents` at a higher generation (`daemon-agent-state.mjs:62`, `lib/residents-role.mjs:14`). comms admits that (`service/api_core/agent_state_shadow.py:162-163`) and replaces the rows (`partial_status_shadow.py:130`), so those keys do not strand. A dedicated scope is single-use (`lib/instance-context.mjs:48,69`): nobody publishes under it again, so its entry stays in the mirror until comms restarts. Every defined agent goes ambiguous because the managed population lists every definition that no resident record claims (`lib/agent-state-host.mjs:224-230`). So K and K' both hold a row per defined agent, and `record_for` counts any row with that agentId on the machine (`partial_status_shadow.py:207-212`) before it checks freshness (`:222`).

**1. Authority: who may say "K holds nothing"**
- **(a) P at a clean shutdown.** Only part of this exists today. The sender stops first (`bin/aify-env.mjs:399,527-532`), and the workers are stopped after that.
  - An empty `stuck` list is not proof. A stop that rejects or throws synchronously counts as settled (`lib/shutdown.mjs:182-187`). `runner.stop` throws when liveness is unknown (`lib/runner.mjs:821-822`) and does not probe again after `killTree` (`:856-858`).
  - A hard kill, or any external stop on Windows, runs no handler (`shutdown.mjs:20-21`).
  - ASSUMED: neither repo shows whether herdr ends a dedicated daemon with a handled signal (`bin:412-418`) or with TerminateProcess. That decides how often (a) can fire at all.
- **(b) A successor that proved it ended P's workers.** This does not exist for any key.
  - Dedicated: the successor skips `reapLeftovers` (`bin:666-669`) and never reads P's private record (`instance-context.mjs:41,49`, recovery `none`).
  - The successor could find K: descriptors are never removed (`lib/instance-descriptor.mjs:13`) and `K.generation` stays on disk. It could also outrank P: `advanceGeneration` (`lib/publication-generation.mjs:29-44`) gives a higher generation, which `_order` admits without checking incarnation. But it cannot prove anything about P's workers.
  - Default: `reapLeftovers` proves nothing either.
    - An unanswered `isAlive` is read as "already gone" (`lib/orphan-reap.mjs:47-57`).
    - A live pid that cannot be verified is left running (`:83-85`).
    - Nothing probes again after `killTree`.
    - Record entries carry no instance, generation or lifetime (`lib/owned-processes.mjs:118-137`).
    - Managed lifetimes exist only in memory (`agent-state-host.mjs:241-245`).
  - The default key does not need (b), because the successor reuses the same key.
- **RULE [B5-2].** Only P retires K, and only the managed key, only at a clean shutdown, after the stops, and only when the typed proof below holds. Residents are never retired. They outlive the daemon, and releasing the role publishes nothing by contract (`residents-role.mjs:13-14`). Successor retirement, (b), is a later slice. It needs an operator ruling on recovery `none`, a three-way proof per record entry, and a link from each lifetime to its record entry. None of these exist.
- **The proof [B5-2].** It replaces the draft's assumed reuse of `refresh` (`agent-state-host.mjs:106-111`). That code judges resident records against the OS, not managed lifetimes.
  - **Population, `published`.** `AgentStatePublisher.#body` (`lib/agent-state-publisher.mjs:103-115`) adds every non-empty `agents[].lifetime` and `removed[].lifetime` to a `#published` set, at numbering time.
    - Numbering happens before the fetch (`agent-state-sender.mjs:174-176` before `:182`). So the set includes bodies that were in flight, aborted by `stop()` (`:93`), unacknowledged, or never sent, any of which may have committed.
    - It is not the agents current now (`current()`), not `Runner.list()`, and not the absence of a lifetime from `#managed`. Absence cannot tell "ended" from "never started".
  - **Ended set, `ended`.** `AgentStateHost.endManaged` adds the lifetime to an `#ended` set right after `this.#managed.delete(lifetime)` returns true (`:249`), before the turns-file branch. A persistence failure (`:252,:260-262`) does not undo an end.
    - Every caller is a confirmed end:
      - `ManagedLifetimes.end` (`lib/managed-lifetimes.mjs:49-58`, contract: observed exit or confirmed death). It is reached from `runner.mjs:306` (observed exit; the error path at `:523` passes `observedDeath=false` and ends nothing), `:824-826` (`isAlive === false`; unknown liveness is refused at `:821-822`) and `:933-934` (`release`, which the reaper calls on a proven-dead pid, `reaper.mjs:105`).
      - `daemon-agent-lifecycle.mjs:74-76`, after `stopAndVerify` has seen `isAlive === false` (`verified-stop.mjs:41`).
    - `Runner.stop`'s path for a live child ends nothing itself. Registry removal at `:823` and a returned `killTree` at `:857` are not death observations. That lifetime counts only once its exit event reaches `:306`.
  - **Type.** `host.retirementProof(published)` returns `{holds: true, published}` or `{holds: false, unproven: [...]}`. It holds only if the host's population is `managed` and every published lifetime is in `ended`.
    - Ownership comes from the type: in a managed host only `#managed` entries can be current (`resident-lifetimes.mjs:140`, `adoptable:false` is never own). Those enter only through `startManaged` (`:241-245`, called from `managed-lifetimes.mjs:43`).
    - So any lifetime that did not come from that path fails the proof.
  - **Nonclaim.** An end judges the pid that Runner holds. Its descendants are covered only by the existing ManagedLifetimes contract. B5 runs no new OS probe.
- **The sequence [B5-2].** A new `afterStops` slot in `createShutdown`, passed through by `daemonShutdownHooks` (`lib/shutdown-hooks.mjs:29-63`), runs after the stops race and the stuck report (`shutdown.mjs:199-212`) and before `clearOwned`/`exit(0)` (`:214-225`).
  - One timer, `RETIRE_DEADLINE_MS = 1000`, is armed as the slot starts. It covers the proof, the registry read, credential reads and every POST.
  - Steps:
    1. Compute the proof. If it fails, write `agent state retirement: not-proven (n lifetimes)`. No body is numbered.
    2. Number the retirement with `publisher.retire()`: n+1. From then on the publisher refuses every `#body`.
    3. POST it to the authorized destinations (see transport).
    4. Write the outcome.
  - `shutdown` awaits `Promise.race([afterStops(), deadline, second signal])`. A second signal reuses the `skipWait` branch (`:93-97`). Then `exit(0)` runs unconditionally.
  - Exit waits for the send for at most 1 s and never longer. Worst case, the whole shutdown takes 2 s + 5 s + 1 s. A request still pending at the deadline is abandoned at exit.
  - **An attempt is not a committed retirement.** `confirmed` means:
    - the proof held;
    - every destination that was ever sent a body is authorized;
    - every authorized destination answered 204, `ok`, not redirected (`agent-state-sender.mjs:186`), before the deadline.

    Anything else is written as `unconfirmed`. aify-env keeps no state from the outcome; only the comms cursor decides whether K is retired. An unconfirmed stop leaves B5 open for that stop (section 4).
- **Transport [B5-2].**
  - **Terminal destination identity.** `stop()` (`:88-94`) clears `#slots` and makes `#current` false for good (`:101`). Before the clear at `:92`, it freezes `#terminal`: name → target for the slots current at that instant. A target's identity is its `signature`: resolved URL, `credentialRef` and `keyEnv` (`:37-38`).
    - `#send` also adds `target.signature` to `#everSent` immediately before the fetch (`:182`).
  - **Which destinations remain authorized.** Re-read the registry inside the deadline. A destination is authorized only if it is in `#terminal` and its fresh registry entry has the same signature. An unreadable registry authorizes nothing.
    - A target removed or changed by reconcile (`:123-127`), or after it, is not authorized for credential egress just because it once received a body.
    - If `#everSent` is not a subset of the authorized set, the retirement still goes to the authorized destinations, but the outcome is `unconfirmed`, `destination-not-authorized`.
  - **Occupied requests.** Physical-URL occupancy outlives abort until the credential read or fetch settles (`:155-157,166-167,195-199`). The retirement neither waits on it nor releases it: it is its own request outside `#occupied`, and it never goes through `#send` or `#current`. Normal publication stays closed (`#stopped` stays true).
    - Abort is not treated as settlement. An earlier aborted body either committed before the retirement (the retirement then deletes it) or arrives after it (refused `stale`, `agent_state_shadow.py:168-169`). Numbering decides which; occupancy does not.
  - **Deadline.** All destinations share one deadline: one AbortController signal per destination, all tied to the slot's single timer.

**2. The wire: a new kind, `retired`, not an empty snapshot**
- **Body:** the common fields (`agent_state_shadow.py:131`), `reason` ∈ {`shutdown`}, and an optional `build`. It carries no `agents` and no `removed`.
- **Numbering:** P's own publisher numbers it, with the same generation and incarnation and publication = last + 1. All slots share one counter (`lib/agent-state-publisher.mjs:85,104`). So every earlier body, including one aborted at stop (`lib/agent-state-sender.mjs:93`) that arrives late, is refused `stale` (`agent_state_shadow.py:168-169`).
- **Why an empty snapshot is refused:**
  - P's enumeration always lists the defined agents, so an empty snapshot would be made up.
  - The receiver keeps the entry and its build (`partial_status_shadow.py:130-139`), and `builds_of` counts it in every later binding (`shadow_provenance.py:92-96`). After a restart onto new code, no window could qualify on one build again.
  - It cannot be told apart from a live P with nothing to publish, so it cannot fence P's later bodies.
  - The fold ignores it, because `env_applied` reads only rows (`delivery_turns.py:232`).
- **Who may publish under K:** any body with a higher generation, whatever its incarnation (`agent_state_shadow.py:162-163`). The same generation must also carry P's incarnation (`:166-167`). A successor's retirement would take its generation from `K.generation`. That is not in this slice.

**3. Effects in comms that the design commits to**
- **Validation:** a `retired` body with `agents` or `removed` gets 422.
- **`_order`:** existing rules, plus a new refusal. If the stored cursor kind is `retired` and the body has the same generation, refuse it as `retired`. A higher generation is admitted, so `default` can be reused after P retired it. The cursor's `kind` column is free text (`schema.py:674`), so no migration is needed.
- **`_write_data`:** delete K's rows (as a snapshot does, `:244-245`) and set the `data_*` columns to the retirement.
- **Mirror:** a new `retire(key)` drops the entry entirely; it does not keep an empty Publisher. After that, `record_for` and `builds_of` no longer see K.
- **Provenance:** a successful retirement does not call `provenance.applied`, the same as for `unavailable` (`:224-225`). The window open at that moment keeps K's build. The next take reseeds from the mirror without it (`shadow_provenance.py:122-129`). A one-build window after that still needs an actual read.
- **Fold:** retirement writes no delivery turn and no origin. Proof that P's workers ended is not a reporter clearing run R, so retirement never produces CLOSED.
- **Fold forget [B5-1].** `FOLD.forget(machine)` drops every `_lineages` and `_fresh` entry on K's machine, and nothing else. `_turns`, `_origin`, `_reported` and `_estimates` stay as they are, and no provenance is applied.
  - **Why not K's last rows:** `env_applied` visits only current rows (`delivery_turns.py:232-240`), and `_withhold` keeps the previous lineage clock (`:255-261`). So a lineage K replaced or removed can keep a baseline that appears in none of K's rows.
  - **Why not a per-key history:** the adviser's all-lineages-per-affected-agent repair needs a historically complete affected-agent population, and current rows do not supply one. A history of every (agent, lifetime) K's consumption wrote would be complete, but it is new state with its own completeness argument.
  - **Why the whole machine covers it by construction:** every baseline is written only in `_consume` (`:266-267`), under `machine = projection.key[0]` (`:230`). So every baseline K could ever have written, or left retained, has K's machine as its first key part.
  - **The reviewer's fixture:** K consumes A/L1 open. K' sends A/L1 closed and is withheld. K replaces A with L2 while K' keeps A/L1. K retires. L1's baseline is dropped, so K' republishing L1 closed reads `first`, not `ended` (`:127-128`), and no CLOSED is written (`:151-152`).
  - **Why estimates are left alone:** an estimate's authority is lost only on an observation that was not consumed (`_withhold`, `:258-261`) or that carried no age. A retirement neither consumes nor skips any observation. So an authoritative estimate stays true, and a new turn first seen without a baseline starts non-authoritative (`_estimate`, `previous is None`).
  - **What this costs:** every publisher on that machine reads its next observation as `first`. A freshness entry is unknown until that lineage's next advance. An env-evidenced end that falls exactly across the forget is missed, and the reporter's clear still closes it. This only loses knowledge. Other machines are untouched.
- **A late old-key body:** refused at the database cursor before any mirror feed (`agent_state_shadow.py:204-207`). This holds across comms restarts, because the cursor is in the database. A higher-generation body under K is admitted.
- **If `retire` or `forget` fails after the commit [B5-3].** The draft's `Mirror()` plus `lose_everything` is wrong. A fresh `Mirror` is an ordinary empty view (`partial_status_shadow.py:103-105`), `bind` calls it non-lost (`:143-155`), and `builds_of` then has no UNKNOWN to report (`shadow_provenance.py:85-97`). Never call `invalidate_safe(K)` either: it keeps K's rows and marks a loss (`:113-116`). The replacement is `shadow.retire_safe(key, before)`, beside `invalidate_safe` (`:385-393`), whose form it reuses:
  - `_retire` captures `before = shadow.mirror.view()` (an immutable mapping, `:107-108`) before it attempts anything.
  - On failure, it installs a new `Mirror` and runs `Mirror.invalidate(new, k)` for every key in `before` except K, on every machine. That is the existing lost entry: no rows, no build, `loss=True` (`:110-117`).
  - Effects of that fallback:
    - K is absent.
    - Every other agent reads `unknown-loss`.
    - `builds_of` holds UNKNOWN for each lost key.
    - `FOLD.lose_everything()` runs (origins become `lost`, `delivery_turns.py:242-253`).
    - `provenance.applied(None)` puts UNKNOWN into the open window.
    - A new `recorder.lost(keys)` adds each key, never `ALL_PUBLISHERS`, to the continuity lost set (`:270`), so the window closes `publication-loss`.
  - **How the loss ends:** each key's loss ends only on its own admitted data publication (`Mirror.feed`, `:129-138`). A `changes` body leaves that key `unknown-knowledge`, because the replaced entry is incomplete. Only its snapshot completes it, and aify-env sends one at least every 60 s (`agent-state-sender.mjs:12,174-176`).
  - **Continuity:** a key recovers only on a completed comparison of that key (`shadow_windows.py:44-53`). One healthy body from another publisher recovers nothing else.
  - **If `before` cannot be read:** the fallback installs a mirror whose `view()` raises. Every `bind` is then a loss: `unknown-loss`, UNKNOWN. That is sticky until comms restarts, because there is no list of keys to wait for. Its scope, `ALL_PUBLISHERS`, can never recover, because no comparison can complete.
  - This is not permission to bring K back, mint CLOSED, add a last-contact authority or implement the reload.
- **Last contact:** nothing stores it today. The cursor has no applied-at column, and `data_applied_at` moves only on data bodies (`:258-261`). On a 204 that committed but whose feed failed, the database has moved while the mirror holds a loss with the old timestamp.
  - B5 adds no last-contact input.
  - If B5b needs one, it is the applied-at of the last committed body under K, of any kind, including feed-failed ones. A refusal (200 with `applied:false`, `routers/agent_state.py:48`), a 422 or a rollback never renews it.
  - Last contact is never evidence for retirement.
- **B5b (reload):** a retired cursor reloads as no entry. Warning: a reload that lands before section 4 has an answer brings back a crashed K's rows from the database. The interim comms restart would then stop clearing the ambiguity.
- **B5c (a publication per turn event):** the per-event path must be gated by the same stop (`bin:527-532`). No event body may be numbered after the retirement; the publisher refuses it.

**4. Crash: no clean shutdown, no successor proof, or an unconfirmed retirement**
This covers a hard kill, TerminateProcess, a stop that cannot be proven, and a retirement that is `not-proven` or `unconfirmed`. K's cursor and rows stay in the database, and its entry stays in the mirror. Every defined agent reads `ambiguous-association` until comms restarts, because ambiguity is checked before freshness (`partial_status_shadow.py:210-212` vs `:222`). That answer is honest: P's dedicated workers may be orphaned and still running, since recovery is `none` and nothing reaps them. The interim stays as it is: restart comms together with aify-env. It does not close B5, and B5 stays open for crashes.

**What changes in the receiver at d6f135fe (branch `098/b5-retired`)**
- [B5-1] `DeliveryTurns.forget(machine, records)` becomes `forget(machine)`. It drops `_lineages`/`_fresh` for every key whose first part is that machine and stops marking `_estimates` non-authoritative (branch `delivery_turns.py:255-264`). `_retire` calls `forget(key[0])` (branch `agent_state_shadow.py:188-189`).
- [B5-3] `_retire`'s `except` (branch `:190-192`) becomes: `retire_safe(key, before)`, `FOLD.lose_everything()`, `provenance.applied(None)`, `recorder.lost(keys)`. `partial_status_shadow.py` gains `retire_safe` and `Recorder.lost`. `Mirror.retire` is unchanged.
- [B5-3] `test_a_failed_retire_trusts_neither_the_mirror_nor_the_fold` asserts an empty view, which is now wrong. It is replaced by the tests below.
- Unchanged: validation, `_order`'s `retired` refusal, `_write_retirement`, `Mirror.retire`, and the other six tests.

**Rollout order**
1. comms receiver first, as amended above. Accepting the new field before anything emits it is a compatibility requirement only. It grants no deployment or switch permission, and it does not override existing operator ordering.
2. Then aify-env: the publisher's `#published` and `retire()`; the host's `#ended` and `retirementProof`; the sender's `#terminal`, `#everSent` and `retire({deadline})`; and the `afterStops` slot.
   - One combined env restart is possible. But the old daemon that is already running has no retirement handler, so its first stop still needs the interim paired comms rebaseline.
   - Any env restart voids the current D-UNKNOWN run.

An aify-env that ships ahead of the receiver gets a 422. That is reported `unconfirmed`; it is harmless.

**Named tests (mutation arm in brackets)**

comms `service/tests/test_publisher_retirement.py` (and `test_status_shadow_windows.py` for continuity):
- `retired_removes_key_and_resolves_ambiguity` [feed it as an empty snapshot]
- `retired_key_contributes_no_build` [keep an empty Publisher]
- `delayed_old_body_refused_after_retirement` [drop the `retired` branch / make retirement terminal for every generation]
- `retired_cursor_survives_a_new_store`
- `retired_validation_422`
- `retirement_forgets_withheld_lineage`: the existing same-lineage fixture. [skip the forget]
- [B5-1] `retirement_forgets_a_replaced_lineage`: the reviewer's fixture (K A/L1 open; K' A/L1 closed withheld; K replaces A with L2; K retires; K' republishes L1 closed). The turn is not CLOSED. [forget K's last rows only]
- [B5-1] `retirement_forgets_a_removed_lineage`: K removes A/L1 by `changes.removed` while K' holds A/L1 closed and has not republished. K retires, K' republishes, and the turn is not CLOSED. [forget K's last rows only]
- [B5-1] `retirement_keeps_an_unrelated_publisher_healthy`. Control: K3 on the same machine holds B/M open with a reporter-open turn, and C/N is on another machine.
  - After K retires:
    - B's turn, origin and `strict_expiry` are unchanged.
    - C/N's baseline survives.
  - K3's next publication of M reads `first`; the publication after it, with M closed, writes CLOSED.
  - [`lose_everything` instead of forget / demote estimates machine-wide]
- `retirement_writes_no_turn_or_origin` [mint CLOSED on retire]
- [B5-3] `failed_retire_leaves_k_absent_and_others_lost`: K, K2 (same machine) and K3 (another machine); `Mirror.retire` raises. Assertions:
  - K is absent.
  - K2 and K3 are lost, with no rows.
  - `builds_of(bind())` contains UNKNOWN.
  - K2's agent reads `unknown-loss`.
  - The provenance take holds UNKNOWN.
  - `recorder.take()` lost == {K2, K3}.
  - K's cursor is `retired`.

  [`Mirror()` alone / install K lost / `invalidate_safe(K)`]
- [B5-3] `one_healthy_publisher_does_not_recover_the_rest`: after the fallback, K3 snapshots and a K3 comparison completes.
  - K2 is still `unknown-loss`, UNKNOWN is still present, and `next_reason` keeps K2 in loss.
  - A K2 `changes` body leaves K2 `unknown-knowledge`; a K2 snapshot recovers it.
  - [record the scope as `ALL_PUBLISHERS`]
- [B5-3] `failed_retire_with_unreadable_view_is_sticky`: every bind is a loss, and a later K2 snapshot does not clear it. [fall back to an empty `Mirror()`]
- `refusal_renews_nothing`

aify-env `tests/shutdown.test.js`, `agent-state-sender.test.js`, `agent-state-publisher.test.js`, `agent-state-host.test.js`:
- [B5-2] `proof_covers_every_numbered_body`: a body numbered and aborted in flight has a lifetime with no end, so there is no retirement. [collect lifetimes on 204 only]
- [B5-2] `proof_rejects_registry_deletion_and_killTree_return`: `runner.stop` on a live child with no exit event yet gives `not-proven`. [use an empty `Runner.list()` / `stuck.length === 0` / absence from `#managed`]
- [B5-2] `proof_accepts_each_confirmed_end_path`: `:306`, `:825`, `release`, and `daemon-agent-lifecycle` `stopAndVerify`; plus a persistence-failed end.
- [B5-2] `proof_refuses_non_managed_population` [retire every sender]
- `retirement_numbered_above_every_body`: same generation and incarnation, n+1; a later `#body` throws.
- [B5-2] `transport_only_terminal_and_still_registered`: a target removed by reconcile, or whose signature changed, gets no credential read and no POST, and the outcome is `unconfirmed`. [send to every target in `#everSent`]
- [B5-2] `transport_ignores_occupancy_and_never_reopens`: an occupied, aborted request does not delay the retirement and is not released by it; `tick()` after it does nothing. [await occupancy / clear `#stopped`]
- [B5-2] `retirement_one_deadline_then_unconditional_exit`: a POST that never answers gives exit at 1000 ms with `unconfirmed`; a second signal ends the wait. [no timer / exit before the await]
- [B5-2] `attempt_is_not_confirmation`: 200 with `applied:false`, 422, an abort or a transport failure each give `unconfirmed`.
- `no_turn_event_body_after_retirement`

### Critical Files for Implementation
- `C:\Users\Administrator\projects\aify-comms-b5\service\api_core\agent_state_shadow.py`
- `C:\Users\Administrator\projects\aify-comms-b5\service\api_core\partial_status_shadow.py`
- `C:\Users\Administrator\projects\aify-comms-b5\service\api_core\delivery_turns.py`
- `C:\Users\Administrator\projects\aify-env\lib\agent-state-host.mjs` (with `lib\agent-state-publisher.mjs`)
- `C:\Users\Administrator\projects\aify-env\lib\agent-state-sender.mjs` (with `lib\shutdown.mjs` and `lib\shutdown-hooks.mjs`)
