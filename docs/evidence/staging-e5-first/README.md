# E5: a fresh host registers a staged dashboard, and env's publication arrives there

2026-11-14. **PASS E5, exit 0** (`run.txt`). All 14 recorded checks passed and cleanup read every run resource back
absent.

E5 covers the seam between env and the dashboard. A fresh host registers a dashboard with that dashboard's own
`npm run register`. env then publishes agent state to the dashboard, with no hand edits. The dashboard bridge inside
an agent session is out of scope and printed as NOT_RUN.

## What ran

- **Sources:** comms `0151beea`, env `c695b52a`, wrapper `8c7d8f43` and aify-dashboard `627fd3fb`. `refs.json` has
  the full SHAs. Only the candidate phase runs; the baseline tuple is the same because the manifest requires one.
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

| Check | What it observed |
| --- | --- |
| `fresh_host_install` | the owner installers exit 0 and name each staged HEAD |
| `dashboard_refuses_wrong_key_and_holds_nothing_before_registration` | 401 for a wrong key, and 200 with no publishers while env runs but is not registered yet: the control for the next two checks |
| `register_writes_entry_with_stored_key` | `register.mjs` exits 0, its output does not contain the key, and `~/.aify/services.json` holds the `aify-dashboard` entry with that endpoint, `agentState.path` `/api/v1/agent-state` and a `credentialRef` (the key went to `aify-env credential set`) |
| `env_publication_arrives_at_registered_dashboard` | the staged dashboard's `/api/v1/agent-state` serves one publisher and all three defined agents |

Receipts redact the dashboard key as `<dashboard-key>` beside `<fixture-key>`. As with the fixture key, only the
operational `candidate-compose.json` holds it.

## Two earlier attempts

Both stopped before the E5 checks, and both cleaned up to absent.
- **Run 1** called `host.installRuntime()`, which needs the comms bridge's `node_modules`. E5 starts no worker, so
  the call was removed.
- **Run 2** installed env alone. `aify-env agents set` refused with exit 65: "the codex launcher is not installed on
  this host". A host gets its launchers from the comms and wrapper installers, so E5 now runs the E4 bootstrap.

## Unit tests and plants

`mcp/stdio/tests/staging-e5.test.js` has 10 cases: the optional role, the layout with and without a dashboard key,
the key rules, and the redaction. `node --test mcp/stdio/tests/staging-*.test.js` passes 210 of 210, these 10 included. `plants.mjs` removes one limb at a
time, and each of its 8 plants turned its named test red (`plants.txt`, 8/8). The sources were restored by copy and
compared byte for byte.

## Reproduce

From the comms checkout, with Git Bash on Windows, and env, wrapper and aify-dashboard checkouts that hold the
selected commits:

```bash
bash scripts/staging-e2e.sh E5 --refs docs/evidence/staging-e5-first/refs.json --env-repo <env> --wrapper-repo <wrapper> --dashboard-repo <aify-dashboard> --out /absolute/path/to/new-run-directory
node --test mcp/stdio/tests/staging-e5.test.js
node docs/evidence/staging-e5-first/plants.mjs
```

The full run record (refs, executed source, input hashes, Docker receipts, rendered configuration, install and
register output, the agent-state read and the cleanup receipts) is kept outside the repository, at
`C:/Users/Administrator/aify-e5-runs/run3/`. Its executed staging source is byte-identical to this commit's.
