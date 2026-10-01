# P5 evidence: `aify-env agents import` and the doctor's definition rows (C10, C11, D12)

P5 is aify-env's, built on `~/projects/aify-env-next` (branch next/env-owned-agents). Its proof across
both repos lives here in aify-comms.

## What it adds

In aify-env:

| file | what |
|---|---|
| `lib/agent-import.mjs` | PURE: `importPlan` (decisions, conflicts field by field, `--prefer`), `parsePrefer`, `definitionCheck`, `planLines` |
| `lib/plugins/aify-comms/agent-import-records.mjs` | PURE: aify-comms' roster row as a definition record; `env` always unreported |
| `lib/plugins/aify-comms/index.mjs` | the `agents` capability gains `importable()` |
| `lib/service-plugins.mjs` | `capabilities(name)`: every started plugin's offer, not the first |
| `lib/protocol.mjs` | `GET /agents/importable`: each service's report, and the ids this host defines |
| `lib/client-actions.mjs` | `importableAgents`, the command's call to the daemon |
| `bin/aify-env-agents.mjs` | the `import` verb: a dry run unless `--write`; `--prefer <service>[:<id>]` |
| `lib/definition-checks.mjs`, `lib/environment-report.mjs` | the doctor's `definitions` and `undefined-agents` rows |
| `lib/plugins/aify-comms/definition-sync.mjs` | C11: each failure logged once per channel; a 404 sets `accepted: false` |
| `bin/aify-env.mjs` | the store moves to module scope so the route and the plugins share it; the route's deps |

In aify-comms: `service/tests/e2e/test_the_roster_reads_as_definitions.py` with `roster_as_definitions.mjs`.

Over 400 lines, and so flagged: `lib/protocol.mjs` (424), `lib/plugins/aify-comms/index.mjs` (473),
`bin/aify-env.mjs` (992, eight under the 1000-line gate).

## Witnesses

aify-env (`tests/`):
- `agent-import.test.js`: the plan's rules one by one, `--prefer`, the check, the lines, and the roster
  mapper (this machine only, the harness derived from `HARNESS_RUNTIME` for every entry, what is
  unreported);
- `aify-env-agents-import.test.js`: the command as a child process against a temporary store and a
  stand-in daemon on an ephemeral port. A dry run leaves the directory byte-for-byte as it was; a
  conflict is listed and not written; `env` is said to be unreported; `--write` writes only the import
  row; a defined id is not overwritten; a service that did not answer blocks the write. In process: an
  id defined between the plan and the write is refused by the store, and a store awaiting recovery
  plans nothing;
- `the-services-report-this-machines-agents.test.js`: the route, `capabilities()`, the client, and the
  real aify-comms plugin's `importable`;
- `definition-checks.test.js`: both doctor rows branch by branch, and the collector asking for them;
- `the-host-publishes-its-definitions-and-applies-requests.test.js`: an old service's 404s logged once
  each, and a failure that clears logged again when it returns;
- `doctor-picker-readiness.test.js`: its healthy daemon now publishes definitions and answers
  `/agents/importable`, so "every row green" still means every row.

aify-comms: the e2e registers three agents with a real service process (one with every field distinct
from its default, one on another machine, one whose runtime has no harness) and reads the roster back
through aify-env's own client and mapper. Against a copy of aify-env whose mapper reads
`row.workspace` instead of `row.cwd`, it failed on the workspace field.

## Not witnessed

`bin/aify-env.mjs` hands the route `agentServices` and `definitions` inside the request handler, which
no test reaches: the bootstrap test evaluates only the plugin block, and the existing `agents:` line
beside them has never been reached either. Running the daemon is the operator's action, so this wiring
is proven only when aify-env runs this build.

## Mutation battery

`mutations-p5.json`, 56 mutants, run with the P2 driver against aify-env-next. Before the battery, six
mutants were predicted to survive the first draft of the tests, and each got a witness: an agent with
no machine id, a plugin that fails to start, the race between plan and write, an invalid row in the
e2e, a store awaiting recovery, and the handler wiring (left unwitnessed, above). One guard was
deleted instead of tested: the plan skipped a service reporting one id twice, which a roster keyed by
id cannot do.

The first run killed 55 of 56. **"C10 cli: unreadable ids count as defined"** survived: no test gave
the command a store with an unreadable file. `aify-env-agents-import.test.js` now does (an id whose
file cannot be read is planned as defined and never written), and that mutant alone was then killed.

`mutations-p5-result.txt` is the whole battery run again after that witness.


## Revision after review (REVISE of 29e4f21 / 6e05e5e3): N5

**N5: an absent field became an empty value nobody was told about.** The mapper marked only `env` and an
absent `herdrSpace` unreported; a roster row without a model, instructions or effort wrote `""` for each,
and the plan named only `env`. Now provenance is per field: a field the row carries as a string is
reported, an empty one included; a field the row omits, or leaves null, is unreported, written as its
neutral value and named. Effort follows the service's own read order: a non-empty `effort`, then
`thinking`, then an explicitly empty `effort`; neither key is unreported.

Witnesses: `agent-import.test.js` (absent, null and explicitly empty, each effort case, and the plan's
"not reported by" line naming every substituted field); the roster e2e now carries a service-produced
`herdrSpace: false`, set through the service's own route, since the mapper's neutral value is true (the
first version's `true` passed with the value forced), and an agent registered with neither a model nor a
runtimeConfig. Measured there: registration stores a missing model as `""` (`req.model or ""`), so the
roster REPORTS it empty, while the missing runtimeConfig leaves effort UNREPORTED. The mapper reads both
as the service produced them.

`mutations-p5r.json`: the 56 of `mutations-p5.json` with four mapper mutants re-anchored to the rewritten
mapper, and four N5 mutants; every mapper mutant also runs the roster e2e. Result in
`mutations-p5r-result.txt`.
