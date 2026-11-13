# Four end-to-end runs on a staging stack

The operator authorised 3–4 end-to-end tests on 2026-11-07: "find good places to do some live test... in some
staging container... so we could sometimes run it and check if all is still good". Unit suites prove each repo
against fixtures. These runs prove the three repos together, on real processes, without touching the live fleet.

## Where they run

A separate compose project, `aify-comms-staging`, on Docker, never on the live host's processes:

- **comms**: its own containers, ports 8820/8821, network `aify-comms-staging-network`, and volume
  `aify-comms-staging-data`. Set `DATA_VOLUME_NAME` explicitly. Without it, a staging stack mounts the LIVE volume
  (docs/superpowers/plans/evidence/2026-10-02-staging).
- **aify-dashboard**: no staging run starts one. aify-dashboard#18 is checked live after the switch instead (see
  2026-11-08-scope-decisions.md, step 6): a staged dashboard plus a Claude file-write fixture measured 190-225
  script lines for what one real file write and one activity read prove.
- **host tier**: a Linux container running aify-env and the launchers from the checkouts under test. Its home
  directory belongs to the container, so its `~/.aify`, leases and owned-process records cannot meet the live
  ones. An aify-env started on this host instead would supersede the live one and reap its managed agents
  (aify-env has no `AIFY_HOME` override; its `lib/residents-home.mjs` says so).
- **runtimes**: the real `codex` and `claude` binaries are installed, but **no run calls a model provider** and no
  host credential enters a container. The work loop is driven by a stub runtime that speaks the same launcher
  contract. The Codex app-server handshake needs no provider (shown on codex-cli 0.166.0 for wrapper#14).
- **one command**: `scripts/staging-e2e.sh <run>` builds the named shas, runs, prints one PASS/FAIL line per
  check, and tears down everything it created, volume included. Evidence is the printed result plus the shas.

## The four runs

| Run | What it does | Fails when |
|---|---|---|
| **E1 fresh install** | Empty host-tier container: `install.sh --client claude` and `--client codex` against staging comms, then `aify-comms doctor --json` and `aify-env doctor`. | a fresh install needs a second pass, a doctor row is red, or an auth-exempt path answers without the key |
| **E2 work loop** | Spawn a managed stub agent through aify-env, send it a request, read the linked reply, stop it, then restart aify-env and check that recovery left no owned process behind. | a spawn answers 201 without a live child, a reply is not linked to its run, a stopped agent's process survives, or recovery kills something it does not own |
| **E3 Codex auth** | `codex-aify` with the real app-server: an unauthenticated WebSocket gets 401, and the comms MCP child connects with the forwarded token. | the app-server accepts no token, or the comms child cannot reach it |
| **E4 switch rehearsal** | Start the stack at the shas live today, seed it with a copy of the live database, then switch to the candidate shas exactly as the joint switch will: rebuild comms, `install.sh`, move the env/wrapper checkouts, restart aify-env. Then run E2's checks. | the new aify-env cannot take over from the old one, an agent record or definition is lost, or doctor is not green after the switch |

### E1 consent contract, amended 2026-11-13

Tech-lead decision for #49: success is one invocation by a consenting interactive operator. In staging, a tty
supplies explicit approval of the shown artifact digest and consent to policy-only sudo preparation. The chain
must finish complete, and the applicable doctors must be green on a run-owned host. Provider/context exclusions
remain explicit. Do not pre-approve the digest or prepare policy in a fixture to manufacture that success.

A separate noninteractive case supplies no consent. It must refuse with exactly one copyable next-action command,
print no key, and publish no registry entry. Check policy readiness through its existing owner before connect;
there is no rollback or second installation path. Exit 78 is never installation success. The existing E1 record
remains RED until the amended successor run is observed. Test Codex in an independent fresh home, not after a
partially completed Claude install. Container source overrides do not qualify GitHub retrieval or a work-PC install.

The amended successor was observed on 2026-11-13: 26 of 26 E1 checks passed on the exact local-source tuple.
[Its evidence and retained failures](../evidence/issue49-e1-consent/README.md) distinguish applicable doctor success
from excluded provider setup, and preserve the earlier red records. This is not a live rollout or a GitHub fetch qualification.

The database copy follows the 2026-10-02 method: the SQLite backup API inside the live container, opened
read-only, then seeded into the staging volume before first start.

## Which issue each run qualifies

| Run | Issues it proves together |
|---|---|
| E1 | comms#43, #49, #27 (dashboard→comms over host-gateway); env#32; wrapper#9, #18, #21 |
| E2 | env#13, #15, #17, #19, #21, #31; comms#31, #53 |
| E3 | wrapper#14 plus the comms allowlist (`scripts/codex-mcp-env-vars.cjs`) |
| E4 | the joint switch itself, including env#17's first takeover from dde9d6d, which only a fixture covers today |

From now on, an issue in this table is not closed on its unit tests alone. Its run must pass on the candidate
shas, and the closing comment names the run and the shas.

## Not covered

Windows: ConPTY, PowerShell launch and native process identity. The live host is Windows, so E2–E4 still need
one supervised pass on the live switch. Also not covered: real model behaviour (model sync, #23), Hermes,
and anything that needs a provider login. Those are qualified on the live switch, as now.

## Order

1. comms builds E3's allowlist change; wrapper#14 publishes as a pair with it.
2. comms builds the harness and E4 first, because E4 de-risks the joint switch everyone is waiting on.
   Then E1, E2 and E3.
3. The joint switch happens after E4 passes on the candidate shas.
