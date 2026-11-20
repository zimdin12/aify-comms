# End-to-end staging runs

Use staging to test selected revisions together without touching the live fleet. A passing run covers
its pinned revisions and scenario. It does not qualify a live rollout or a different build.

## Where they run

Each run uses its own Docker Compose project named `aify-comms-staging-<run-id>`.

- **Comms:** the generated layout gives each run an internal network, a service-data volume and a
  host-home volume, with no published ports. The harness checks that `DATA_VOLUME_NAME` names that
  run's service-data volume. Do not reuse live projects or volumes.
- **Host tier:** a Linux container runs aify-env and the selected launchers. Its home holds the run's
  registry, leases and owned-process records. Starting aify-env on the live host instead can
  supersede the running instance and reap its managed agents.
- **Dashboard:** the layout adds a dashboard service only when the manifest pins a dashboard
  revision. E5 requires it and runs the server source on the run's host image with its own volume and
  fixture key. E5 does not exercise the dashboard's Dockerfile or its bridge inside an agent session.
  Dashboard file-write activity is a separate check.
- **Runtimes:** no run calls a model provider or uses host credentials. The work loop uses a stub
  that follows the launcher contract. E3 uses a real Codex app-server for its auth handshake.

`scripts/staging-e2e.sh <run> --refs <manifest>` uses pinned sources, prints a result for each check,
and records cleanup for the resources it created. Keep the receipts with the source revisions.

## Scenarios

| Run | What it checks | Fails when |
|---|---|---|
| **E1 fresh install** | Install Claude and Codex clients on a clean host, then run the applicable Comms and Env doctor checks. | A consented install needs a second pass, an applicable doctor check fails, or a protected route answers without the key. |
| **E2 work loop** | Start a managed stub through aify-env, send work, read the linked reply, stop it, then exercise recovery. | The child never starts, the reply is not linked to its run, or recovery leaves or reaps the wrong process. |
| **E3 Codex auth** | Verify the real app-server requires the per-launch token: it refuses a WebSocket with no token or a wrong one, and answers `initialize` with the right token. The token is absent from readable process argv, from one sampled agent response and from Codex's config. The Comms allowlist names both token variables. NOT_RUN: the MCP child using the token, and how a populated runtime record names the variable, because both start only on a model turn. | The app-server accepts a missing or wrong token, refuses the right one or leaves `initialize` unanswered, an allowlist name is missing, or the token appears in a sampled argv, response or config. |
| **E4 switch rehearsal** | Seed a synthetic Linux fleet on a pinned baseline, switch to the pinned candidate, then check takeover, work delivery and retained records in that stack. | Env cannot take over, a seeded record is lost, or an applicable doctor check fails after the switch. |
| **E5 dashboard registration** | Register a staged dashboard from a fresh host, then read Env's publication and check it again after settlement. | The dashboard accepts a wrong key, the registry entry is wrong, or an expected agent is missing, stale or in conflict. |

### E1 consent contract, amended 2026-11-13

For #49, success is one invocation by a consenting interactive operator. In staging, a TTY supplies
approval of the shown artifact digest and consent to policy-only sudo preparation. Check policy
readiness through its existing owner before connect. Do not pre-approve a digest or prepare policy
in a fixture to make the run pass.

A noninteractive case supplies no consent. It must refuse with exactly one copyable next-action
command, print no key, and publish no registry entry. Exit 78 is not installation success. Test Codex
in a separate fresh home, not after a partially completed Claude install.

The [E1 evidence](../evidence/issue49-e1-consent/README.md) records 26 passing checks on the pinned
local-source tuple and keeps the earlier failures. It does not qualify GitHub retrieval or a work-PC
install.

### E4 and the disk reserve

Every manifest, for every scenario, must set `seed: "synthetic-linux-fleet"` and
`liveCopyEnabled: false`: the harness builds the manifest before it dispatches a scenario. E4 does
not copy the live SQLite database. Before invoking any rehearsal, verify the Docker data filesystem has at least
40 GiB free. On this host, read `/data` with `docker exec aify-comms-service df -Pk /data`. The
harness can pull its probe image before reporting space, so its capacity check does not replace that
read-only measurement. Check for the `aify.staging.run` label on containers, volumes, networks and
images. Run only one rehearsal at a time. An empty listing is not a lock or permission to delete
resources.

Dry `--preflight` checks the manifest and generated layout. It does not measure free space, run
Docker or exercise the switch.

### E5 publication evidence

The [E5 evidence](../evidence/staging-e5-first/README.md) records the pinned Node 24 run. It checks
that a wrong key returns 401 and that the dashboard has no publishers before registration. After
registration, one fresh managed publisher must hold every expected agent without conflicts. The
only other publisher allowed is the one Env announces for residents, and every returned publisher
must be fresh. The scenario reads the state again after a full reporting interval. Reuse this evidence for those revisions only, not for current disk capacity.

## Which issues each run qualifies

| Run | Issues it covers |
|---|---|
| E1 | comms#43, #49, #27 (service Host admission only; the dashboard container's host-gateway DNS half is NOT_RUN); env#32; wrapper#9, #18, #21 |
| E2 | env#13, #15, #17, #19, #21, #31; comms#31, #53 |
| E3 | wrapper#14 and the Comms allowlist (`scripts/codex-mcp-env-vars.cjs`) |
| E4 | The isolated Linux component switch and Env takeover |
| E5 | Dashboard registration and Env publication on the staging host image |

Unit tests alone do not qualify these integration scenarios. Record the run, pinned revisions and
applicable exclusions before calling one qualified.

Windows ConPTY, PowerShell launch, native Windows process identity, real model behaviour, Hermes,
and provider login are outside these rehearsals. A passing E4 is not permission to deploy or restart
the live stack.
