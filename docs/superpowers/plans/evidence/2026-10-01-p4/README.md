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
  The behaviour that matters, that a resume restarts the loop, is killed by its own mutant
  ("a resume does not restart the sync").

`mutations-p4-result.txt` is the whole battery run again after that change.

## Two seams P4 broke, and what changed

The first full run after the battery read 1 failed and 2 new skips in the Python suite, all P4's:

- **The three host routes had no request model.** The push, the claim and the result read their
  bodies by hand, so `test_the_env_plugin_addresses_routes_this_service_serves.py` had no declared
  names to check the plugin's keys against and failed ("carries a body and matched no route with a
  model"). `service/definition_models.py` now declares them, every field `Any`, so api_core's named
  refusals still decide and 422 never pre-empts them. Removing `snapshotDigest` from the push model
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
