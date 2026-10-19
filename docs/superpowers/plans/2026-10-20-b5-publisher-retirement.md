# B5: retiring a superseded agent-state publisher, with aify-env as the authority (DRAFT)

**Where B5 lives in the code.** A managed key is `default` or `herdr-<invocation>` (aify-env `lib/daemon-agent-state.mjs:17-19`). The residents key is `residents-<hash12>` (`lib/residents-home.mjs:72-75`). Successors reuse `default` and `residents` at a higher generation (`daemon-agent-state.mjs:62`, `lib/residents-role.mjs:14`). comms admits that (`service/api_core/agent_state_shadow.py:162-163`) and replaces the rows (`partial_status_shadow.py:130`), so those keys do not strand. A dedicated scope is single-use (`lib/instance-context.mjs:48,69`): nobody publishes under it again, so its entry stays in the mirror until comms restarts. Every defined agent goes ambiguous because the managed population lists every definition that no resident record claims (`lib/agent-state-host.mjs:224-230`). So K and K' both hold a row per defined agent, and `record_for` counts any row with that agentId on the machine (`partial_status_shadow.py:207-212`) before it checks freshness (`:222`).

**1. Authority: who may say "K holds nothing"**
- **(a) P at a clean shutdown.** Only part of this exists today. The sender stops first (`bin/aify-env.mjs:399,527-532`), and the workers are stopped after that.
  - An empty `stuck` list is not proof. A stop that rejects or throws synchronously counts as settled (`lib/shutdown.mjs:186,190`). `runner.stop` throws when liveness is unknown (`lib/runner.mjs:821-822`) and does not probe again after `killTree` (`:856-858`).
  - A hard kill, or any external stop on Windows, runs no handler (`shutdown.mjs:21`).
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
- **RULE.** Only P retires K, and only the managed key, only at a clean shutdown, after the stops and after proof. Proof means: every non-null lifetime P put in any body this boot is judged verified `no` by the host's own lifetime judgement (`agent-state-host.mjs:106-111`). ASSUMED: that judgement can be reused here. Any `unknown` means no retirement.
  - Residents are never retired. They outlive the daemon, and releasing the role publishes nothing by contract (`residents-role.mjs:13-14`).
  - Successor retirement, (b), is a later slice. It needs an operator ruling on recovery `none`, a three-way proof per record entry, and a link from each lifetime to its record entry. None of these exist.

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
- **Provenance:** do not call `provenance.applied` for a retirement, the same as for `unavailable` (`:224-225`). The window open at that moment keeps K's build. The next take reseeds from the mirror without it (`shadow_provenance.py:122-129`). A one-build window after that still needs an actual read.
- **Fold:** retirement writes no delivery turn and no origin. Proof that P's workers ended is not a reporter clearing run R, so retirement never produces CLOSED.
  - It does forget baselines: for each (machine, agent, lifetime) in K's last mirror rows, remove the `_lineages` and `_fresh` entries and mark estimates non-authoritative.
  - This closes the traced conditional path. While K and K' both hold an agent, `_withhold` keeps K's last consumed clock (`delivery_turns.py:255-261`). Without forgetting, a later observation of the same lineage reads `ended` (`:127-128`) from history the fold never consumed. After forgetting, it reads `first`.
  - Lineage keys carry no instance, so forgetting may also drop a K' baseline for the same lineage. That only loses knowledge.
- **A late old-key body:** refused at the database cursor before any mirror feed (`agent_state_shadow.py:204-207`). This holds across comms restarts, because the cursor is in the database. A higher-generation body under K is admitted.
- **If `retire` fails after the commit:** rebuild from scratch (a new `Mirror()` plus `FOLD.lose_everything`). Never call `invalidate_safe(K)`: it keeps K's rows and adds a loss (`partial_status_shadow.py:113-116`).
- **Last contact:** nothing stores it today. The cursor has no applied-at column, and `data_applied_at` moves only on data bodies (`:258-261`). On a 204 that committed but whose feed failed, the database has moved while the mirror holds a loss with the old timestamp.
  - B5 adds no last-contact input.
  - If B5b needs one, it is the applied-at of the last committed body under K, of any kind, including feed-failed ones. A refusal (200 with `applied:false`, `routers/agent_state.py:48`), a 422 or a rollback never renews it.
  - Last contact is never evidence for retirement.
- **B5b (reload):** a retired cursor reloads as no entry. Warning: a reload that lands before section 4 has an answer brings back a crashed K's rows from the database. The interim comms restart would then stop clearing the ambiguity.
- **B5c (a publication per turn event):** the per-event path must be gated by the same stop (`bin:527-532`). No event body may be numbered after the retirement.

**4. Crash: no clean shutdown, no successor proof**
This covers a hard kill, TerminateProcess, or a stop that cannot be proven. K's cursor and rows stay in the database, and its entry stays in the mirror. Every defined agent reads `ambiguous-association` until comms restarts, because ambiguity is checked before freshness (`partial_status_shadow.py:210-212` vs `:222`). That answer is honest: P's dedicated workers may be orphaned and still running, since recovery is `none` and nothing reaps them. The interim stays as it is: restart comms together with aify-env. It does not close B5, and B5 stays open for crashes.

**Rollout order**
1. comms receiver first: validation, `_order`, write, `Mirror.retire`, fold forget, provenance. Deploy it.
2. Then aify-env: `publisher.retire()`, `sender.retire()` usable after `stop()`, and a new after-stops slot in `createShutdown`. That slot runs after the stops race (`shutdown.mjs:199`) and is bounded to at most 1 s. Exit never waits on it.

An aify-env that ships ahead of the receiver gets a 422, which is a harmless `not-applied` notice.

**Named tests (mutation arm in brackets)**

comms `service/tests/test_agent_state_receiver.py`, `test_agent_state_partial_shadow.py`, `test_delivery_turns.py`, `test_shadow_b6a.py`:
- `retired_removes_key_and_resolves_ambiguity`: K and K' both hold A, which reads ambiguous; after `retired` under K, A reads K's sibling's record. [feed it as an empty snapshot: the build test fails]
- `retired_key_contributes_no_build` [keep an empty Publisher]
- `delayed_old_body_refused_after_retirement`: a lower publication gets `stale`; the same generation with a higher publication gets `retired`; a higher-generation snapshot is admitted. [drop the `retired` branch / make retirement terminal for every generation]
- `retired_cursor_survives_a_new_store`
- `retired_validation_422`
- `retirement_forgets_withheld_lineage`: K consumed (A,L) open; K' holds (A,L) closed and is withheld; K retires; K' republishes; the turn is not CLOSED. [skip the forget]
- `retirement_writes_no_turn_or_origin` [mint CLOSED on retire]
- `retire_failure_rebaselines`: no rows from K survive. [call `invalidate_safe(K)`]
- `refusal_renews_nothing`: cursor and `data_applied_at` unchanged after a refusal.

aify-env `tests/shutdown.test.js`, `agent-state-sender.test.js`, `agent-state-publisher.test.js`:
- `retires_only_after_proven_end` [use `stuck.length === 0` as the proof]
- `rejected_or_unknown_stop_blocks_retirement`
- `retirement_numbered_above_every_body`: same generation and incarnation, n+1.
- `residents_sender_never_retires` [retire every sender]
- `retirement_bounded_exit_unconditional`
- `no_turn_event_body_after_retirement`

### Critical Files for Implementation
- `C:\Docker\aify-comms\service\api_core\agent_state_shadow.py`
- `C:\Docker\aify-comms\service\api_core\partial_status_shadow.py`
- `C:\Docker\aify-comms\service\api_core\delivery_turns.py`
- `C:\Users\Administrator\projects\aify-env\lib\shutdown.mjs` (with `bin\aify-env.mjs` and `lib\shutdown-hooks.mjs`)
- `C:\Users\Administrator\projects\aify-env\lib\agent-state-sender.mjs` (with `lib\agent-state-publisher.mjs`)
