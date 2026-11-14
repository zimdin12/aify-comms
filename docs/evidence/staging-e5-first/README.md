# E5: a fresh host registers a staged dashboard, and env's publication arrives there

2026-11-14. **PASS E5, exit 0, on Node 24.22.1** (`run.txt`, run7). All 15 recorded checks passed, and cleanup read
all 12 run resources back absent. The publication check names each publisher and requires no agent in conflict.

E5 covers the seam between env and the dashboard. A fresh host registers a dashboard with that dashboard's own
`npm run register`. env then publishes agent state to the dashboard, with no hand edits. The dashboard bridge inside
an agent session is out of scope and printed as NOT_RUN.

## What ran

- **Sources:** E5 is composed on comms `18e9281e`, the capacity stdout repair; run7 ran that composed tree.
  The other refs are env `c695b52a`, wrapper `8c7d8f43` and aify-dashboard `627fd3fb`; `refs.json` has the full
  SHAs. The comms SHA staged inside the run is `0151beea`. Only the candidate phase runs; the baseline tuple is the
  same because the manifest requires one.
- **Node:** `node:24.22.1-bookworm@sha256:d862bb5d…`, E5 only. aify-dashboard `627fd3fb` declares
  `engines.node >=24.15`, so E5 pins a Node 24 image. Node v24.22.1 was released 2026-10-20, and that tag's digest
  was last pushed 2026-10-28. E1-E4 keep `node:22.20.0-bookworm`. In E5, env, the wrapper and the comms bridge run
  on Node 24 too: that is extra coverage, not part of their qualification.
- **The staged dashboard:** the run-owned service `aify-dashboard`. It runs the dashboard's own server source
  (`server/src/main.ts`) on this run's host image, with its own volume (`<project>-dashboard-data`) and its own
  random key, distinct from the fixture key. It has no published port and is reached only on the internal network as
  `http://aify-dashboard:9700`. It does not use the dashboard's Dockerfile, because that image runs as `node` and
  declares `VOLUME /data`, and the harness's isolation checks refuse both. NOT_RUN says so. That image is
  exercised outside this harness instead: every deploy of the live dashboard on this host builds and runs it (last
  at aify-dashboard `9e600de1`, 2026-11-14).
- **The host:** the fresh host E4 starts from. `fixtures/bootstrap-host.sh` runs env's, comms' and the wrapper's own
  installers. The check reads that each of comms, env and wrapper was installed from its exact staged HEAD.

## The checks E5 adds

| Check | What it observed in run7 |
| --- | --- |
| `host_node_meets_dashboard_engine` | the host's `node --version` (v24.22.1) meets the staged dashboard's `engines.node` (`>=24.15`); any range other than `>=X.Y[.Z]` is refused |
| `fresh_host_install` | the owner installers exit 0 and name each staged HEAD |
| `dashboard_refuses_wrong_key_and_holds_nothing_before_registration` | 401 for a wrong key, and 200 with no publishers while env runs but is not registered yet: the control for the next two checks |
| `register_writes_entry_with_stored_key` | `register.mjs` exits 0, its output does not contain the key, and `~/.aify/services.json` holds the `aify-dashboard` entry with that endpoint, `agentState.path` `/api/v1/agent-state` and a `credentialRef` (the key went to `aify-env credential set`) |
| `env_publication_arrives_at_registered_dashboard` | `publicationHolds`, read when it first holds and again 65 s later (`agent-state-after.json` in the run record) |

`publicationHolds` requires:
- exactly one publisher other than the residents instance named in env's health (`residents.publisherInstance`,
  counted only while `held`), and that publisher holds every defined agent;
- every publisher fresh;
- no defined agent reading as a conflict.

With no residents role held, any second publisher is refused. Env publishes its managed population under its own
state instance, separately from the residents role's instance (env `lib/daemon-residents.mjs:4-13`). In run7 both
reads served the same two publishers:
- `default`, fresh, with the 3 defined agents;
- `residents-8ba3bc6c7335`, fresh, with no agents.

All 3 agents read `conflict: false`.

Receipts redact the dashboard key as `<dashboard-key>` beside `<fixture-key>`. As with the fixture key, only the
operational `candidate-compose.json` holds it.

## Earlier runs

- **Run 1** called `host.installRuntime()`, which needs the comms bridge's `node_modules`. E5 starts no worker, so
  the call was removed.
- **Run 2** installed env alone. `aify-env agents set` refused with exit 65: "the codex launcher is not installed on
  this host". A host gets its launchers from the comms and wrapper installers, so E5 now runs the E4 bootstrap.
- **Run 3** passed every check then in E5, on `node:22.20.0-bookworm`. That is below the dashboard's declared
  engine, so it does not establish a supported install.
- **Run 4**, the first on Node 24, refused at `daemon_capacity` with "NaN GB free": the capacity probe read Docker's
  pull status instead of `df`'s number on a first pull. comms `18e9281e` repairs this; E5 does not touch it.
- **Run 5** passed on Node 24 with the earlier publication check, which needed only a publisher count above zero.
  It served 2 publishers and kept only the count. Run7 identifies the two.
- **Run 6** was stopped by the author's runtime for host memory pressure during the image build. It left no labelled
  resources and was not a result.

Runs 1, 2, 4 and 6 stopped before the E5 checks. Every run cleaned up to absent.

## Unit tests and plants

`mcp/stdio/tests/staging-e5.test.js` has 29 cases:
- the optional role;
- the layout with and without a dashboard key;
- the key rules and the redaction;
- 10 version and engine pairs;
- 9 publication cases.

`node --test mcp/stdio/tests/staging-*.test.js` passes 235 of 235 on the composed tree, these 29 included.
`plants.mjs` removes one limb at a time, and each of its 19 plants turned its named test red (`plants.txt`, 19/19).
The sources were restored by copy and compared byte for byte.

## Reproduce

From the comms checkout, with Git Bash on Windows, and env, wrapper and aify-dashboard checkouts that hold the
selected commits:

```bash
bash scripts/staging-e2e.sh E5 --refs docs/evidence/staging-e5-first/refs.json --env-repo <env> --wrapper-repo <wrapper> --dashboard-repo <aify-dashboard> --out /absolute/path/to/new-run-directory
node --test mcp/stdio/tests/staging-e5.test.js
node docs/evidence/staging-e5-first/plants.mjs
```

The full run records (refs, executed source, input hashes, Docker receipts, rendered configuration, install and
register output, the agent-state reads and the cleanup receipts) are kept outside the repository, under
`C:/Users/Administrator/aify-e5-runs/`. Run7 is current, run5 is the Node 24 count-only pass, and run3 is the
Node 22 history. Run7's executed staging source and refs are byte-identical to `4d9ed4bf`, the commit before this
README.
