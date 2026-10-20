# Retiring a dead publisher's key: owner-authorized, exact-key (design rev 2, 0.9.8 pre-switch)

Asked for by the SW1 verdict (message 1792531860902-a0541994). Revised for
`REVISE_DESIGN_D48B57B0_OWNED_WORK_COMPLETENESS` (1792535068876-25b1d614). It is a design only: nothing is built,
swept or cleaned up. The wired switch stays on HOLD.

## The problem, and the answer to "is it provable today"

comms restored 12 publisher keys for `win32:stevenz-l` at start (803ede80), and 10 of them are dead `herdr-<invocation>`
keys (appendix A). SW2 knows an agent only when every holder is fresh and no publisher on its machine is unreadable
(`operational_view._busy`). A dead single-use key never becomes fresh again: `readInstanceContext` refuses a used
invocation. So after the switch, sends to those agents would queue. **The truth table stays as it is.**

**Completeness is NOT provable from today's records.** A dead key is retired only when every unit of work it owned is
proven ended, and no record on disk today is a complete population of that work:
- **Spawn before record.** `Runner.start` spawns the child, then `register`, then `recordStarted`. A daemon death
  between those steps leaves work that is in no file.
- **Swallowed writes.** `write` in `lib/owned-processes.mjs` catches every failure. A failed `recordStarted` leaves the
  previous readable array, `[]` included, while the child runs. A strict reader cannot tell this from "nothing started".
- **Stop before kill.** `Runner.stop` calls `#release`, which runs `recordStopped`, before the tree kill. A crash or a
  failed kill leaves live work absent from the file.
- **Roots are not the work.** The recorded pid is the launcher. The agent is its child, and the stop comment in
  `Runner.stop` records agents surviving a launcher-only kill. A dead root proves nothing about its descendants.
- **Turn records.** `#managed` and `#ended` in `AgentStateHost` are in memory. `<key>.turns.json` holds only lifetimes
  that had turn events.
- **comms' rows.** They miss work started after the last publication. A row with no pid makes a pid-coverage check
  vacuous.

Therefore **all 10 keys in appendix A stay unproven** under this design. Their disposition is a separately authorized
operator action, not covered here and not a bare forget: the cursor row is the fence (see "The fence").

## The smallest owner-side record that makes it provable: a write-ahead lifetime ledger

`~/.aify/env/<key>.lifetimes.jsonl` is append-only and written durably (as `writeFileDurably` does). It is owned by
the key's daemon, and each entry names the lifetime:
1. **`intent`** `{lifetime, agentId}` is written BEFORE `Runner.start` spawns. If the write fails, the spawn is refused:
   no work exists that the ledger does not name. Today's rule is the opposite: start the work and leak the record.
2. **`pin`** `{lifetime, pid, createdAtUs, role}` is written for the root after spawn. It is written again for every
   process that identifies with the lifetime through its `AIFY_LIFETIME` carrier on the hook route; that process is
   the agent, the descendant that matters. The route must record the reporter's pid and creation time. **ASSUMED
   feasible, not checked.**
3. **`stopping`** `{lifetime}` is written where `#release` runs today. It does not remove anything.
4. **`ended`** `{lifetime}` is written only after every pin of that lifetime is confirmed gone. That is
   `ManagedLifetimes.end`'s "observed exit or confirmed death", extended from the root to every pin.

**Completeness evidence.** Every lifetime the key ever started has an `intent`, because no spawn happens without one.
The population is the set of `intent` entries. A ledger that is unreadable, has a torn last line, or is missing for a
key that has an `owned-processes.json` is unknown, and nothing is retired.

**Limit, stated.** A process that neither the launcher pin nor an `AIFY_LIFETIME` report names is outside every record.
This design does not claim it.

## The death proof (all required; any null, unreadable or unjoinable fact refuses)

The retirer is the residents-role holder only (`ResidentsRole`, inside `enter()`/`live()`), one per `AIFY_HOME`. It
acts on exact `herdr-<uuid>` keys of this home. It never retires `default`, the residents key, or its own key.
1. **The daemon is gone.** The descriptor pid equals `claimed.json`'s pid. The probe (`answersFrom`,
   `lib/process-probe.mjs`) answers `alive: false`, or `alive: true` created after the descriptor's `startedAt`.
2. **The herdr owner pipe** `\\.\pipe\aify-herdr-owner-<uuid>` answers "does not exist". Any other error is unknown.
3. **Every `intent` lifetime has ended.** Either it has an `ended` entry, or every pin it has is gone or reused against
   its OWN `createdAtUs`. A lifetime with an `intent` but no pin (a crash during start) is unknown, and so is one where
   only the root is pinned but the root is alive. Both refuse.

## The body, the fence and races

**Canonical facts.** The body carries only facts read from the ledger and the key's own files, never probe answers,
which change from one run to the next:
- `machineId`, `instance` = the key, `generation` = saved + 1;
- `incarnationId` `"predecessor:<key>"`, `publication` 1, `reason` `"predecessor-ended"`;
- `inputs.lifetimes`: the sorted `lifetime=pid@createdAtUs` list of every pin of every `intent` lifetime.

Two retirers reading the same ledger therefore build identical bytes. Probe results go to the retirer's log, not into
the body.

**The fence.** Every frame the dead key numbered has `generation <= saved` (`advanceGeneration` saves before the first
push). `_order` refuses each one as `stale` against the kept retired cursor. There is no forget and no delete.
- **A saved generation below comms' cursor.** If `saved + 1` is below the cursor, the body is `stale`. If it equals
  the cursor, the body is `conflict` (a different incarnation id), or `duplicate` (identical bytes already applied).
  Each one retires nothing. That is reported, and the retirer never invents a higher generation.
- **Two retirers despite the role** (a handover). With an unchanged ledger, the second gets `duplicate`. If the ledger
  changed, the second gets `conflict`. Both keep the first retirement.

## comms: one reason and a join

`RETIREMENT_REASONS` (`service/api_core/agent_state_shadow.py`) gains `predecessor-ended`. comms applies such a body
only when both gates pass:
- **(a) Freshness.** The key is not fresh within the receipt window (Steven's decision 01M5D9WR).
- **(b) Join by lifetime.**
  - Every last-known row of the key with a non-null `lifetime` names a lifetime in `inputs.lifetimes`.
  - Its non-null `process.pid` is one of that lifetime's pins.
  - A row with a pid but no lifetime cannot be joined, and the body is refused.

The join binds each row to its own process generation, never to a bare pid number.

Copies that change together:
- comms `RETIREMENT_REASONS`;
- env: a pure body builder beside `AgentStatePublisher.retire`, plus the ledger writer in `Runner` and the hook route;
- the cross-repo fixtures `service/tests/data/g6_agent_state_wire.mjs` and `test_agent_state_receiver.py`.

aify-wrapper holds no copy, by a grep that does find the env and comms copies. The env change merges only after the
comms and wrapper suites pass with `AIFY_ENV_REPO` set to the candidate.

## The counterhistory, and what this design does not claim

The earlier claim that a misretirement needs "two independent wrong answers" is **withdrawn**. The reviewer's
omission history needs no wrong answer: the record write fails, the daemon dies, and the worker survives. Here that
history is closed by the ledger's write-ahead `intent` and by the refusal of anything unpinned. It is not closed by
comms' freshness gate. A misretirement still releases a claim beside a fresh idle peer, and nothing here makes that safe.

## Witnesses (each watched RED before the fix)

| # | specimen | expected |
|---|---|---|
| W1 | the ledger write fails before spawn | the spawn is refused; no work, no lifetime |
| W2 | today's history: `recordStarted` fails, leaving a valid `[]`, and the worker lives | unproven: the key has no ledger |
| W3 | death after `intent`, before `pin` | unproven |
| W4 | stop: `stopping` written, root killed, agent pin alive | unproven |
| W5 | root dead, member pin alive | unproven |
| W6 | old-ended-P / new-live-P: L1 pin P@t1 ended, L2 pin P@t2 alive | unproven (L2) |
| W7 | unrelated reuse: L1 pin P@t1, OS shows P created at t3 with no ledger entry | proven |
| W8 | all `intent`s ended (control, otherwise W2-W6) | proven, and retired |
| W9 | a comms row with a pid but no lifetime / a lifetime absent from the body | refused |
| W10 | the live key, `default`, the residents key, with every file readable | never a candidate |

## Steven's decisions (open, not asked by this page)

1. Automatic retirement by the role holder's sweep, or an operator verb sharing the same proof.
2. The receipt window that gate (a) uses (01M5D9WR).
3. The disposition of the legacy keys in appendix A, which can never be proven, including f17d5dd4's recorded pid 29316.

## Appendix A: observed population (not a cleanup manifest)

These are observations from the 2026-10-20T21:31Z comms start log and a read-only file listing. No pid was re-probed.
**It authorizes nothing.** Any action needs its own authority, a fresh exact-key ledger entry and a fresh read of the
live state.
- live: `herdr-c0912a9d-4247-48a0-b1ad-a29fbd22e54d` (pid 84272), and `residents-1212b94bbe48`
- dead, with no ledger, hence unproven:
  - `herdr-f17d5dd4-b166-4709-8a7f-74fd3aeadfa5`: `owned-processes.json` names pid 29316, `claude-aify`, owner 73960
  - `herdr-9821d407-95ef-4bbd-8c22-2b0a8a16eb6e`, `herdr-78cfcc61-d9a3-49ec-967a-0759ecbcf347`,
    `herdr-069e3559-fa62-4e79-a01e-b9940b28acd2`, `herdr-b68089e7-0bf4-4598-935e-e32805fda441`,
    `herdr-20ecafd8-463a-4739-b0ae-6cfeecf8b8c5`, `herdr-9add1a1b-e8a6-4055-b840-ec5907c50137`,
    `herdr-70d0e9be-74d4-4218-8bc7-3f00eca1fee7`, `herdr-ff4ad8b8-be99-4bfb-b960-e17e80174455`,
    `herdr-0d278add-c0ec-401b-aa94-7a4f9295ebb8`: their `owned-processes.json` files are 3 bytes. Per R1, that does
    not prove an empty population.
