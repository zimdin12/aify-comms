# P4 evidence: aify-env publishes its definitions, applies requests, checks starts (C3, C4, C7)

P4 is aify-env's half, built on `~/projects/aify-env-next` (branch next/env-owned-agents, commit
`89a8b37`). Its proof
across both repos lives here in aify-comms.

## What it adds

In aify-env:

| file | what |
|---|---|
| `lib/agent-definition-requests.mjs` | PURE: `requestDecision` (C4 steps 1 to 4), `mergePatch`, `isRemoval`, `trashedPair`, `startRefusal` (C7's boundary), `HARNESS_RUNTIME` |
| `lib/agent-definitions.mjs` | `applyRequest` (decision and write in one lock hold), `snapshot({fresh})`, and `appliedRequest` on a reading; `set` and `remove` split into in-session bodies |
| `lib/plugins/aify-comms/definition-sync.mjs` | `DefinitionSync`: claim, apply, report, then push; the interval, incomplete snapshots, `FREE_SINCE` |
| `lib/plugins/aify-comms/api.mjs` | `pushDefinitions`, `claimDefinitionRequests`, `reportDefinitionRequest`, `startAgent` |
| `lib/plugins/aify-comms/index.mjs` | the third loop (`syncForever`), `checkStart` handed to the control pass, the store handed to the starter |
| `lib/plugins/aify-comms/terminal-controls.mjs` | `startTerminal` asks `checkStart(launch)` before anything else |
| `lib/plugins/aify-comms/handle-book.mjs` | the handle book, moved out of terminal-controls.mjs (983 lines, now 867) to make room, re-exported there |
| `lib/plugins/aify-comms/agent-starter.mjs`, `lib/startable-agents.mjs` | a defined agent is listed and started by its definition |
| `bin/aify-env.mjs` | the store, made outside the statements the bootstrap tests evaluate; `definitions` and `installedHarnesses` in the plugins' shared inputs |

In aify-comms:
- `service/tests/e2e/test_an_offline_request_reaches_the_hosts_file.py` with `definition_host.mjs`;
- `service/tests/test_the_host_and_service_share_the_definition_vocabulary.py`.

Over 400 lines, and so flagged: `index.mjs` (468), `bin/aify-env.mjs` (989, still under the
1000-line gate), `agent-definitions.mjs` (about 700).

## Witnesses

aify-env (`tests/`):
- `agent-definition-requests.test.js`: the pure rules step by step, then `applyRequest` on a real
  store: a change, a removal, each applied twice, and the refusals;
- `a-start-runs-only-the-definition-it-was-built-from.test.js`: the boundary through `runOneControl`
  with a real store;
- `the-host-publishes-its-definitions-and-applies-requests.test.js`: the sync, with a real store and
  a recording api;
- `the-plugin-carries-this-hosts-definitions.test.js`: the wiring, through the real plugin;
- `a-defined-agent-is-started-from-its-definition.test.js`: startability and the starter;
- additions to `aify-comms-api.test.js` (each call's method, path and body),
  `production-picker-bootstrap.test.js` (the daemon's store and installed harnesses reach the plugin)
  and `plugins-follow-the-registry.test.js` (the sync stops while held and turns again on resume).

aify-comms:
- the e2e test runs the real service as a process. Node runs aify-env's store, client and sync, a
  fresh process for each visit of the host: publish, then the operator asks while the host is away,
  then the host returns. It reads back the file, the request and the service's revision. With the
  sync made to apply nothing, it failed (`assert 0 == 1`).
- the vocabulary test imports aify-env's two pure modules through node. Rewording `FREE_SINCE`, or
  renaming the runtime of the `hermes` harness on aify-env's side, failed each test.

## Mutation battery

`mutations-p4.json`, run with the P2 driver against aify-env-next. Two mutants (the harness table and
`FREE_SINCE`) also run the aify-comms vocabulary and e2e tests. The first run killed 62 of 64:

- **"an applied request waits for the interval"** survived because the request witness handed its
  request out on the first pass, when a push was due anyway. The request now arrives on a second pass
  inside the interval, and that kills it.
- **"the sync runs while held"** (`runs: phase.controls` for `phase.claims`) was removed as
  equivalent. Each pass checks `phase.claims` again after its setup and returns before touching the
  service, so the mutant only keeps an idle loop alive while held, and a resume finds it running.
  **This was wrong** (see "Revision after review", N3): the held loop still begins its tracked setup,
  and a final detach waits for it.
  The behaviour that matters, that a resume restarts the loop, is killed by its own mutant
  ("a resume does not restart the sync").

`mutations-p4-result.txt` is the whole battery run again after that change.

## Two seams P4 broke, and what changed

The first full run after the battery read 1 failed and 2 new skips in the Python suite, all P4's:

- **The three host routes had no request model.** The push, the claim and the result read their
  bodies by hand, so `test_the_env_plugin_addresses_routes_this_service_serves.py` had no declared
  names to check the plugin's keys against and failed ("carries a body and matched no route with a
  model"). `service/definition_models.py` now declares them, every field `Any`, so api_core's named
  refusals still decide and, for a JSON object, never pre-empts them (see the revision's note on other bodies). Removing `snapshotDigest` from the push model
  failed the gate by name. The e2e test drives all three through a real service process, so the
  models accept what aify-env sends. One change in behaviour, observed through a TestClient on the
  push route: a malformed, non-object or empty body now gets FastAPI's 422 before api_core runs,
  where it was a 400 (or, for an empty body, api_core's named refusal). Still refused, under a less
  specific name; aify-env never sends any of the three.
- **The held-terminals seam skipped.** It looked for `export function heldTerminalIds` in the text of
  terminal-controls.mjs, and the handle-book move took the definition to handle-book.mjs (re-exported).
  It now asks node for the module's export. Against aify-env-next it runs (2 passed); against a copy
  with the re-export removed it skips by name (2 skipped).

`suites-p4.txt` is the run after both.

## Revision after review (REVISE of 89a8b37 / cb265f16)

The review's report is `C:/Users/Administrator/AppData/Local/hermes/cache/scratch/89a8b37-cb265f16-P4-review/REVIEW.md`
(sha256 `b94966ba…a908376`). Three blockers, each fixed and witnessed:

**N1. The start boundary held nothing.** `startTerminal` asked once, through a `list()` that released
the store, before the process existed; and `Runner.start` awaits its checkpoint loader before it makes
the child. A set, removal, or removal and re-definition landing in either gap started the old
revision's worker. Now `DefinitionStore.admitStart(launch, produce)` reads the file under the store's
lock and HOLDS IT until `produce` (the plugin's `processes.start`) returns, so a write commits before
the reading (and the start is refused) or after the child exists. The early check is gone; the one
admission wraps the only call that makes a process. A launch built from no definition is produced
without the store. Witnesses (`a-start-runs-only-the-definition-it-was-built-from.test.js`): each of
set, remove and recreate, written through a second store during a paused `processes.start` and during a
real Runner paused in its checkpoint loader; the file at the moment the child is made is the launch's
revision, and the write lands after. The pre-fix shape (check, release, produce) fails all six. A
launch from no definition starts while another start holds the store.

Two consequences, stated: the store is held for as long as `produce` takes, so a checkpoint loader that
hangs would hold it too (today that hang already blocks the start); and the refusal now comes after the
workspace, launcher and live-worker checks, so an ADOPTION (re-pointing a running worker at a new
terminal, which makes no process) is no longer refused for a stale stamp.

**N2. An owed removal waited for a report that never came.** The result route commits the host's
receipt, then removes the agent; a failure between them left the request `done` with its consequence
`pending`, and the host claims only pending and claimed requests, so it never reported it again.
`service/reconcilers/owed_removals.py` now finishes every owed `done` removal from the stored state, in
the reconcile pass, through `finish_removal` and so through its `settled_refusal` and custody fences.
Witnesses (`test_definition_change_requests.py`): the reviewer's sequence (receipt committed, removal
failed, the host's timed push withdrawing the definition) ends with the agent removed and tombstoned,
once; an owed removal whose definition moved to another store removes nothing and says why; one that
keeps failing leaves the others finished and is finished by a later pass; and the reconcile pass runs
the step.

**N3. The dropped mutant was not equivalent.** `runs: phase.controls` keeps the held sync loop turning;
each pass begins its tracked setup (the advertisement read) before its inner `phase.claims` check, and a
final detach waits for that setup. The mutant is restored, and
`plugins-follow-the-registry.test.js` gains the witness: timers fired by hand, the sync's interval fired
while held with the advertisement parked; no setup begins, and the final detach completes once the last
worker ends. The restored mutant fails exactly that witness. The earlier claim, above, that it was
equivalent was wrong: the inner re-read protects service calls, not setup progress.

**A wiring test that read this machine's PATH.** `the-plugin-carries-this-hosts-definitions.test.js`
resolved `claude-aify` from the real PATH and reached its definition refusal only because the old
check ran before launcher resolution. With the refusal at the boundary, it met the operator's own
launchers in `~/.local/bin` and failed on their missing marker. It now carries a temporary launcher
with the marker. It had been red during the first revised battery run, so four kills that rested on it
alone were hollow, and `mutations-p4r-result.txt` is the whole battery run again after the fix.

**The request models' wording.** "422 never pre-empts them" was too broad. For a JSON object, api_core
still decides every field by name. A malformed, non-object or zero-byte body is refused by FastAPI first
(400 to 422; a zero-byte claim or result from a named 409 to 422).

### Revised battery

`mutations-p4r.json`: the 63 of `mutations-p4.json`, six re-anchored to the code as it now reads (the
N1 rename, and P5's `#failed` and second daemon `definitions:` line), the restored N3 mutant, and five
N1 mutants: 69 of 69 killed. `mutations-p4r-service.json`: five N2 mutants against aify-comms. The
first run killed four: removing the sweep's rollback survived, because the failing stand-in raised
before opening a transaction. It now fails as `remove_agent` can, inside `BEGIN IMMEDIATE` after a
write, and the witness checks that write was rolled back; the mutant then failed it, and the rerun
killed five of five. Results in `mutations-p4r-result.txt` and `mutations-p4r-service-result.txt`.


## Second revision (REVISE of 29e4f21 / 6e05e5e3): N4

N1 and N3 were closed, and N2's history repaired. **N4: an owed removal that keeps failing starved the
newer ones.** The sweep read the oldest fifty by `finished_at, id` every pass, and a failure kept those
keys, so fifty that kept failing were the fifty read for ever. Now a failed attempt is rolled back and
then stamped (`definition_requests.consequence_failed_at`, a migration like `consequence`, `''` when it
never failed), and the sweep reads never-failed removals first and the oldest failure next. Passes stay
bounded at fifty; the fences and the rollback are unchanged.

Witness (`test_definition_change_requests.py`): fifty-one owed removals over the default budget; the
fifty oldest fail on every pass inside their own transaction after a write; after three ordinary passes
the newer one is removed, the fifty stay owed with every partial write rolled back, and a pass after the
fault clears finishes them. Both mutants (age-only ordering; no stamp) fail exactly that test.

`mutations-p4r2-service.json` is the five N2 mutants and the two N4 mutants; result in
`mutations-p4r2-service-result.txt`.
