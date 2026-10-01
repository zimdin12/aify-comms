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

## The review of 12766276 (three P3c failures) and P3c-2 (the C5 edit routes)

Witnesses in `service/tests/test_definition_change_requests.py`, each red on an exact 12766276
worktree:
- `test_a_report_skips_no_claim`;
- `test_a_repeated_report_runs_its_consequences_once`;
- `test_a_removal_racing_a_change_of_custody_stops_nothing`, which holds the write lock from outside
  while custody moves.

The edit routes: `service/tests/test_editing_a_defined_agent_becomes_a_request.py`, each defined case
paired with an undefined control. The dashboard says "requested in aify-env" for an answer carrying a
request (`service/new_dashboard/definition-request-note.mjs`, tested in Node; the four call sites are
DOM glue).

`mutations-p3c.json` grew to 53 (an earlier draft of this line said 50), covering the conversions
and the three fixes. `witnesses-red-on-12766276.txt` records the red runs, and corrects its own first
explanation of the interruption witness.

## The review of c8029614 (REVISE: N3, N4, N5)

Its explanation of the predecessor was also wrong, and is corrected in `witnesses-red-on-12766276.txt`:
12766276 committed the host's report before removing, so the two were never one transaction.

**N3, an assignment queued against the wrong machine.** The route judged the environment against the
owner it read, then queued against whoever owned the agent when the queue's transaction ran.
`assignment_for_its_host` now reads the owner, judges the environment against it and queues, all in one
write transaction. Witnesses (`test_editing_a_defined_agent_becomes_a_request.py`):
- `test_custody_cannot_move_between_the_owner_read_and_the_queue`: right after the owner is read, a
  competing writer cannot take the write lock. The same probe run first, outside the transaction, is
  the control showing it can report "not blocked";
- `test_an_assignment_after_custody_moved_is_judged_against_the_new_owner`, where custody moves first;
- `test_environment_assignment` keeps the same-machine and undefined controls.

**N4, two reports could both settle.** A report that read `pending` and then waited could overwrite
another report's settled consequence, or run a removal that the settled record says was not made. The
settle now writes only while the consequence is `pending`. The removal's fence (`finish_removal`, moved
out of the router) first asks `settled_refusal`, inside the transaction that decides. Witnesses:
- `test_a_report_that_waited_does_not_rewrite_a_settled_consequence`, the reviewer's schedule: a
  release, then another machine's acquisition, then the waiting report resumes, and the whole row is
  unchanged;
- `test_a_report_that_waited_runs_no_removal_its_request_no_longer_owes`, where the same store pushes
  the same lifetime again, so the custody fence alone would allow.

The before-removal and after-deletion recoveries keep their witnesses.

**N5, a receipt from before the column.** `service/legacy_removal_receipt.py` reads what each 12766276
receipt recorded:
- its trailing `[service: ...]` note is a refusal, kept as the first note says it;
- a tombstone with no row is `removed`;
- a receipt that recorded nothing is owed (`pending`).

`legacy-removal-upgrade.py` makes all three receipts with 12766276's own routes, in a worktree at that
commit. The `keeper` receipt is the reviewer's case: a refusal, then the same store defines the agent
again at the same lifetime. The script then upgrades the same file with the successor and repeats each
report. `legacy-removal-upgrade.txt` is that run: the refusal stays and its agent survives, the removed
one stays removed, and the owed one is removed by the next report.

The suite holds the same cases in `test_a_12766276_database_keeps_what_each_receipt_recorded`, and the
pure reading in `test_legacy_removal_receipt.py`.

`mutations-p3c.json` now has 63 mutants: the earlier 53, three for N4, one for N3 and six for N5
(the migration mutant, re-anchored, and five on `legacy_removal_receipt.py`). Two older entries were
re-anchored where N3 moved their code.

## P3d: a defined agent starts from its definition (C6, C7)

| file | what |
|---|---|
| `service/api_core/definition_start.py` | `start_binding` (withdrawn, invalid, unavailable and resident refused), the spec built from the definition, and the guarded insert |
| `service/api_core/dispatch_start.py` | the cold start: binding, the defining machine, the twin guard (`twin_refusal`) |
| `service/api_core/session_restart.py` | `_bound_restart_spawn`: a defined agent's restart and recreate |
| `service/routers/terminals.py` | the launch's `definition` |

Witnesses: `service/tests/test_a_defined_agent_starts_from_its_definition.py`. `mutations-p3d.json`
has 33 mutants, 33/33 killed. Writing them found four branches that had no witness, each given one:
- an earlier session on another machine;
- a restart whose session is on another machine;
- a stale binding in the restart;
- a changed harness started from a message.

One branch had no possible witness and was removed: the cold start took a bound start's role and name
from the definition, and for a defined agent its row already holds the definition's (C5).

The repo's refusal gate (`test_every_refusal_is_exercised.py`) then found one more: the restart's
refusal when no environment of the defining machine is online. It has a witness and a mutant (33rd).

## The review of 8de83233 (REVISE: N6)

**N6: the undefined restart from an old spec skipped the guard.** It read "never defined" and then
wrote its own INSERT, so a push or a withdrawal that committed after the read was answered with a
start from the old spec. The direct spawn also wrote its own insert; it was safe only because it holds
the write lock. Both now go through `insert_spawn_request`. The overtaken restart answers 409, and the
route's single commit means nothing it wrote before is kept. Witnesses
(`test_a_defined_agent_starts_from_its_definition.py`):
- `test_an_undefined_restart_overtaken_by_a_definition_queues_nothing`: restart and recreate, each
  overtaken by a push and by a withdrawal right after the binding read. Each is refused, with no new
  request and the agent's native session handle kept;
- `test_an_undefined_restart_from_its_old_spec_and_one_defined_first`: the two serialised orders.

`test_every_start_goes_through_the_guarded_insert.py` walks every product file under `service/` and
allows a raw `INSERT INTO spawn_requests` only in `definition_start.py`. Its pattern is checked
against the forms an insert takes. `n6-red-on-8de83233.txt` is the run on 8de83233's exact tree: four
arms red, both controls green, and the gate naming the two other writers. P3d's battery has 34 mutants,
the 34th for N6.

**A mutant that killed by syntax error.** P3c's "the fence is not asked before the worker is stopped"
left an `if` with only comments in its body, so pytest stopped with exit 2: a kill that tests nothing.
It now replaces the body with `pass`. Run alone, three named custody witnesses fail on behaviour. Every
recorded kill in the four batteries is now checked to be exit 1.

The first full run after N6 left two P3d mutants NOT APPLIED: N6's edit wrote the same lines into the
undefined branch, so their anchors matched twice. They are anchored on the bound restart now. Every
anchor in the four batteries is checked to match exactly once.
