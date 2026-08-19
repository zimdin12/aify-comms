# v0.6 Phase 4 — bughunt findings ledger

Every row is FIXED, DEFERRED with its reason and the bar for revisiting, or NEEDS-RULING with the
question stated. Method as the plan requires: a failing test before every fix, every fix
mutation-proven, ranked by whether a reader could act on a wrong belief rather than by how clever the
bug is.

**Two of the three items that were blocking this phase turned out to need a ruling; the third was
already fixed and my note saying otherwise was stale.** The rulings were delegated by the operator.

---

## FIXED

### 1. A notice the service wrote about a dead agent counted as that agent producing (#10b)

**What a reader could believe wrongly.** That a dead agent had just produced something — on the
roster, at the moment it died, which is when the field is read.

`_mirror_missing_dispatch_handoff` tells a sender their target never answered, and authors that
message AS THE TARGET, because `from_agent` is what threads the notice into the right conversation.
The row is otherwise identical to a real one: same `source='direct'`, same table, same shape.
`_get_outbound_activity_map` reads `MAX(messages.timestamp) WHERE from_agent = ?` and calls the answer
"last produced". So the system NOTICING an agent was dead advanced that agent's productivity clock.
The module's own docstring says "only what it SENDS evidences that it is running".

**Ruled:** fix the reader, not `messages.source`. `source` is binary and about ten readers treat
`'direct'` as "a DM" — analytics, claim gating, run reports, managed-worker sweeps. A third value
changes all of them to fix one. `dispatch_runs.handoff_message_id` already marks these notices, so no
schema change either. Failed and cancelled only: a completed run's handoff carries the target's own
result.

**Measured, because the file demands it** (its docstring records a 3,500× spread and says assuming
does not work here). Against a copy of the live database — 46 agents, 31,363 messages, 19,230 runs:

| shape | median | p95 |
|---|---|---|
| plain query, covering index | 1.91 ms | 3.05 ms |
| `+ NOT IN (SELECT handoff_message_id …)` | 126.59 ms | 251.51 ms |
| `+ NOT EXISTS`, with a new index | 77.69 ms | 164.13 ms |
| guard in Python, sargable probe | **1.99 ms** | 3.99 ms |

Both SQL forms lose `SEARCH m USING COVERING INDEX`: the guard needs `m.id`, which that index does not
carry. 40–66× on the dashboard poll path, to correct **two rows in 31,363** and change **zero** agents
today. So the guard runs in Python over only the excluded notices belonging to the agents asked about.

A second measurement inside it: the probe uses `r.status IN (...)` bare rather than
`LOWER(COALESCE(...))`, because wrapping the column drops the plan to `SCAN r` and costs 52.41 ms
instead of 1.99 ms — the whole saving, given away for defensive syntax.

### 2. A rename left terminals naming a tombstoned id

`terminal_sessions` was the ONLY `agent_id` in the schema neither repointed nor cascaded; it has no
foreign key to `agents`, so deleting the old row never reached it. The renamed agent looked
consoleless while a dead id owned a terminal still reading `running`.

**Ruled: repoint — and I ruled the other way first.** My initial reasoning was that the PTY belongs to
a bridge that still knows the old id, so a repointed row would show the new identity a console its
bridge had never heard of; better to stop them. The schema corrected me:
`terminal_sessions.session_id → agent_sessions(id)`, and `agent_sessions.agent_id` is repointed by the
same rewrite. The terminal's own session already belongs to the new agent, so leaving `agent_id`
behind preserved no truth — it split a child from its parent inside a transaction whose whole job is
to move every reference at once. Status carries across untouched: whether that PTY is alive is a
question the terminal reconcilers answer from state.

Three gates moved with it, including retiring the guard test that deliberately failed when the gap
closed. It said, in words, "if the last gap closed, delete this test with the entry".

---

## ALREADY FIXED — my record was stale

### 3. The managed-register guard did not guard the omitted case

Recorded as open since `9aebbfcc`. It is not: the guard compares the RAW `sessionMode`, so an omitted
mode and an explicit `"managed"` are both refused and only an explicit `"resident"` passes.
`mcp/stdio/tests/managed-register-guard.test.js` exercises the omitted case through the real handler.
The note claiming otherwise was the stale artifact, not the code.

---

## DEFERRED — specimen hunted, none found

The plan says Phase 4 is where the specimen hunt happens, because "a bughunt across live data is
where one would turn up". It was run against a copy of the live database rather than reasoned about.

### 4. Three deliberate fail-opens in `env_status` (§4c)

Flip bar for all three: **a real carrier row**. Result: **zero**, across all 5 environments.

| specimen sought | found |
|---|---|
| an environment whose stored status is empty/falsy | 0 |
| an environment whose status is whitespace-only | 0 |
| an environment whose `last_seen` does not parse | 0 |

Every stored status is one of `online`, `offline`, `forgotten` — all three declared in
`ENVIRONMENT_STATUSES`. The rulings stand, now with a measurement behind them instead of an argument.

### 5. The latent 429/529 false positive (§4d)

Bar: an observed error string that trips the predicate wrongly. **60 stored texts trip it. All 60 are
recognised as service-authored and skipped.**

This is the more interesting half. The rows carry the RETIRED wording — "presumed dead (model 429,
mid-turn interrupt, or stall)" — written before the 2026-08-18 fix, and `is_service_authored()`
compares by identity against the CURRENT constants. It still recognises them, because `_FRAGMENTS`
matches "Failed by reconcile so the run isn't stranded", which those rows also contain. That fragment
was added so the check would survive a consumer prefixing a run id or clipping for display; it also
made the guard survive its own rewording, which nobody claimed at the time. Verified by running the
real predicate over the real stored text, not by reading it.

### 6. The five Lows comms-senior-dev declined to promote (§4b)

Verdicts carried unchanged. One is now closed rather than carried: **"a failed require-reply run can
double-mirror"** was to be promoted *only if* the already-mirrored gate did not close it. It does —
`_already_mirrored` reads `handoff_message_id` OR `result_message_id` for failed and cancelled runs
specifically, and its own docstring records the anti-vacuity test that caught the narrower version.

---

## Method findings — my own tools, not the product

Recorded because this repo's expensive mistakes are measurement mistakes, and three happened here.

1. **A hand-rolled import sweep gave three different wrong answers** — multi-line `export {}` blocks
   read as missing, comments inside import braces read as imported names, then a non-greedy match
   spanning two import statements. Abandoned rather than fixed a fourth time: the suites already prove
   the import graph resolves, and the standing rule is to use the repo's own parser instead of writing
   one. The first version reported 26 "missing" exports, every one of them false.
2. **A mutation check that never ran.** The anchor used `\n` against a CRLF file, so the mutation did
   not land and four tests "passed" a proof that had not happened. Caught by asserting the replacement
   changed the file. Any mutation proof in this repo must assert that the mutation applied.
3. **`INSERT OR IGNORE` swallowed a NOT NULL violation** in a test seed, and the failure surfaced two
   statements later as a foreign-key error on a different table. OR IGNORE hides exactly the mistake
   it looks like it is guarding against.

---

## Scope this phase did NOT cover

Stated so it is not mistaken for a clean bill:

- A fresh sweep of the **MCP tool surface**, **schema/migrations**, and the **installer** beyond what
  Phase 2 rebuilt. Phase 2 rewrote the installer's wrapper half and added executable tests for it;
  the rest of `install.sh` was not hunted.
- The **dashboard** was covered by Phase 3 rather than here, and its own ledger records what remains.
- **Provider-refusal classification** (§4a) stays unbuilt: its entry bar is retained-frame evidence,
  and this phase produced none.
