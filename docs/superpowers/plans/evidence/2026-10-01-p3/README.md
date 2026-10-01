# P3 evidence: the service side of aify-env-owned agents

## P3a: the C3 push, withdrawal, operator release and reset, D13 list fields

What it adds, all in aify-comms:

| file | what |
|---|---|
| `service/api_core/definition_snapshot.py` | PURE: the canonical form shared with aify-env, entry and snapshot shape, the fence, the ordering table (`PushOrder`) |
| `service/api_core/definition_push.py` | one push, in the caller's transaction: refusals, store order, per-id ownership, upsert, withdrawal |
| `service/routers/agent_definitions.py` | `PUT /environments/{id}/agent-definitions`; operator `POST .../definition-store/reset` and `POST /agent-definitions/{id}/release` |
| `service/api_core/definition_records.py` | the `definition` object on the agent list and detail (D13) |
| `service/routers/agents/rename.py` | refuses to rename a defined agent (its id is its host's file name) |

The witnesses are `service/tests/test_agent_definition_push.py`. They drive the real route and read
`agent_definitions` and `agents` back, not only the status code.

### Mutation battery

`mutations-p3a.json`, 54 mutants, run with the P2 driver:

```bash
AIFY_ENV_REPO=<aify-env checkout> MUTATE_TIMEOUT=120 \
  python docs/superpowers/plans/evidence/2026-10-01-p2/mutate.py . \
  docs/superpowers/plans/evidence/2026-10-01-p3/mutations-p3a.json
```

`mutations-p3a-result.txt` records 54/54 killed, each with the pytest test that killed it. The first
run killed 47. The seven survivors were gaps in the tests, each closed by a witness added in the same
commit:
- an invalid entry with no problem;
- an unavailable entry with the wrong reason;
- a definition naming another id;
- an agent defined again after withdrawal;
- the runtime a new agent is filed under;
- a push withdrawing another machine's ids;
- the state a released agent reads.

The driver gained one line for this battery: it now records pytest's `FAILED` lines as well as node's
`not ok`, so a pytest kill is named. P2's node battery prints no `FAILED ` lines, so its recorded
result is unchanged. Each run checked the seven mutated sources against SHA-256 hashes taken before
it; all matched.

### The review of 4af344c5 (REVISE, four findings) and the successor

The same battery grew to 87 mutants after the fixes. It is killed 87/87, each kill named, including
subtest kills (`SUBFAILED`, which the driver now also records):
- R1: release reads its owner inside its write transaction and names the machine it releases.
- R2: the service admits a valid entry by C1. `definition_schema.py` is checked against aify-env's
  fixture, 75 bodies; the C1 rule mutants are its own.
- R3: the digest's presence, type and domain are checked first.
- R4: a replay repeats its revision's unresolved outcome, re-judged and never applied.

Three runs left survivors, each decided on its own:
- A mutant removing a `type(v) is str` guard on harness and mode was equivalent: no non-string
  equals a string in Python. The guard and that mutant were removed.
- A replay's `kept` report had no witness; it has one now.
- The digest-mismatch mutant survived because admission refused the test's lie for its role, not its
  digest. The lie now uses a role C1 admits.

### The cold-start role test

`service/tests/test_a_cold_start_keeps_the_agents_role.py` was held back while it was red. It turned
out to cover every agent, defined or not, so its fix went to main as 0.7.8 (6c2367bf) and reached
this branch by merge. `role-reset-RED-before-P3.txt` records the red on the code before that fix.

### Suites

`suites-p3a.txt`.

## P3b: a defined agent's description is its definition's (C5)

`mutations-p3b.json`: 18/18 killed, each by the witness for its own path
(`service/tests/test_a_defined_agents_description_is_its_definitions.py`).

## P3c: change requests, the service's half (C4)

| file | what |
|---|---|
| `service/api_core/definition_requests.py` | admission, claim, report, the removal fence |
| `service/routers/definition_requests.py` | the four routes |
| `service/api_core/agent_remove.py` | the DELETE route's stop-then-tombstone removal, moved so a removal request's `done` runs it behind the fence |

Witnesses: `service/tests/test_definition_change_requests.py`. `mutations-p3c.json` has 32 mutants,
32/32 killed on its first run, each by a named test. Three witnesses were added before that run,
because nothing yet tested what they cover:
- a patch `{"remove": 1}`, which Python's `1 == True` would read as a removal;
- the fence's second asking, inside the deleting transaction, shown with a fence that changes its
  answer;
- a refusal at the first asking leaving a managed worker running.

## The review of a171e8a3 and b5ac1de3 (REVISE: N1, N2)

**N1, a P3a database.** `p3a-database-upgrade.py` makes a database with 4af344c5's own code (a
worktree at that commit): A owns coder, and B's revision 1 is applied with coder refused. The
successor then initialises the same file twice and replays. `p3a-database-upgrade.txt` is that run:
- the column is absent after 4af344c5 and present after the successor's init;
- the old revision's replay answers 200 with `outcomeRecorded: false`;
- revision 2 records the refusal, and its replay reports it.

`service/tests/test_a_p3a_database_gains_the_replay_outcome.py` holds the same check in the suite,
including the 500 before init.

The live service's database was read on 2026-10-01, read-only, from inside its container:
`/data/aify.db` has 26 tables, among them `agents`, and none of `agent_definitions`,
`definition_stores` or `definition_requests`. So no deployed database needs this upgrade; it exists
for review databases and any other P3a copy.

**N2, the direct spawn.** The spawn route opens BEGIN IMMEDIATE before it reads the owner. The
witness holds the write lock from outside and records whether the owner was read before or after the
competing definition committed. It sees exactly one read, after the commit, then a 409 with no
request and no spec. A spawn-first control queues and then the definition applies.

The batteries grew: P3a has three N1 mutants (90), P3b has one N2 mutant (19).
