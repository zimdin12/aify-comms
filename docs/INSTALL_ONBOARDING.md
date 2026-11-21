# Agent-led installation and updates

An agent can follow this guide across repositories, ask for the missing choices, install the selected
components and verify the result. Each repository installs its own product. aify-comms points to
[aify-env's guide](https://github.com/zimdin12/aify-env); that guide points to
[herdr's own installer](https://github.com/herdrdev/herdr#install) for optional panes. A plain Comms
install hands off to Env's guided chain. The standalone Comms path installs the selected client
only. This follows the
[repo-owner contract](superpowers/plans/2026-09-07-herdr-as-the-pane-surface.md#installing-it-every-repo-installs-its-own-and-points-at-the-next).

## Optional Herdr scope

The available `aify-env herdr` adapter attaches to existing workers only. It does not launch an env-first Herdr workspace, populate native available-agent rows, automatically synchronize worker workspaces, or restore aify launches after reboot. Full integration is on hold while plugin-style and other extension options are evaluated; installing the adapter does not complete it. Read [aify-env's current Herdr contract](https://github.com/zimdin12/aify-env/blob/main/docs/HERDR.md) before offering this option.

## Start with the selected repository's instructions

An older installed skill is not the update authority. For an existing installation, including
v0.5.6, read this guide from the selected current checkout before running its installer. A release
tag and the default branch can contain different fixes even when a package VERSION is unchanged.
Report the chosen commit as well as the version. A successful client reinstall does not update the
service container, environment daemon or already-running workers.

A user who wants the current default-branch fixes can give their agent this prompt:

> Read the current main branch's docs/INSTALL_ONBOARDING.md. Inspect this installation, then install
> or update the components this host needs from current main. Preserve my configuration and data.
> Ask about missing choices and optional Herdr or LAN HTTPS. Report installed and running revisions
> separately, and ask before any action that interrupts the service or running agents.

This is an agent-guided workflow, not an all-in-one unattended installer. The agent must follow the
owning repositories and verify each selected component. The legacy-upgrade fixture in
`scripts/tests/test_legacy_upgrade.py` checks the standalone client upgrade from old installer-produced
configuration. It substitutes runtime CLIs and package/network operations. It does not exercise the
full guided chain or qualify a real host.

## Upgrading a host from before 0.6

A host installed before the three-repo split (v0.5.x) upgrades through the guided chain. This is an
upgrade, not a fresh-install check. See the [older rehearsal](evidence/2026-10-28-upgrade-rehearsal/README.md)
for its exact versions and gaps.

1. **Inventory the host before changing it.** In the shell that will run the installer, follow
   [Discover and choose](#discover-and-choose). Record which commands resolve first, the npm global
   prefix, the selected runtime profiles, endpoint, hooks, and the old bridge and launchers. On WSL,
   read the Linux paths, not the Windows copies. Preserve an existing Herdr installation unless an
   update is selected. Find a legacy bridge with `pgrep -af -- "[-]-environment-bridge"`; its presence
   can make Comms' `env-bridge` doctor row pass before Env is running. Check `spawn-delegation` as well.
2. **Back up and agree on the cutover.** Keep the current config, launcher paths and service revision.
   For a service upgrade, take a SQLite backup and open a restored copy with the old version before
   proceeding; copying only a live `aify.db` can miss its write-ahead log (WAL). Use the same Comms
   commit for the selected service build and client checkout. Resolve any foreign or linked npm
   installation at its source, using the named refusal rather than overwriting it. Have the operator
   stop the old agents and legacy environment bridge before the cutover; verify the old bridge no
   longer runs. Starting the new Env and relaunching agents are separate decisions.
3. **Move the legacy skill trees aside.** For each selected client, confirm its actual skill root and
   identify the old Comms trees using [the migration names below](#migrating-skills-copied-by-an-older-installer).
   The operator moves those trees intact to a backup outside every skill search root. Preserve
   unrelated skills and Env-managed trees. If Env reports another conflict, stop and resolve the
   named path before retrying; do not delete trees or edit receipts.
4. **Run one guided pass per selected client.** Use `bash install.sh --client <runtime> <endpoint>`
   from the selected Comms checkout, once for Claude and once for Hermes if both are selected. Use
   a real terminal to review each artifact approval and any separate Claude channel-policy prompt.
   Without a terminal, an unapproved artifact stops the run; pass only the reviewed digest in
   `AIFY_INSTALL_APPROVE=<digest>`. Address Comms by IP, `localhost`, or `host.docker.internal`.
   Other hostnames need `TRUSTED_HOSTS`; Docker Engine clients may also need a `host-gateway` mapping.
5. **Verify the install, then verify what is running.** Check the installed paths, launchers, skill
   placement and both doctors. Keep unrelated MCP configuration in place. After the approved Env
   start and agent relaunches, read back the running revisions and confirm the old bridge is still
   absent. For a runtime that has never been set up, finish its first-run setup before a managed
   launch. On Linux and WSL use `herdr-aify env` from a real terminal if panes were selected;
   `aify-env herdr` is Windows-only. Run without a terminal, `herdr-aify env` leaves a herdr server and an
   aify-env running; `herdr-aify env --stop` cleans up. The herdr server fetches herdr.dev/agent-detection
   at runtime, which a work network may block. Do not treat this upgrade as proof of a fresh install.

For Claude, the installer removes the old `aify-comms` tool entry and the launcher adds Streamable
HTTP per launch. Hermes uses Streamable HTTP for the tools as well. Codex still uses the local stdio
bridge for tools and delivery. Keep Codex on stdio for the upgrade; the installer removes and
recreates its entry. [Codex catch-up](CODEX_CATCHUP.md)
tracks the separate transport migration.

aify-env and herdr after that upgrade were rehearsed separately on Linux ([aify-env evidence 786f957](https://github.com/zimdin12/aify-env/tree/786f957/docs/evidence/2026-10-28-work-pc-rehearsal)):
with aify-env started, the comms doctor reads 14 passed and 3 skipped. Not covered: WSL itself, a
logged-in agent doing work, and hermes on Linux.

## Discover and choose

1. Inspect this host's OS, architecture and shell. Distinguish native Windows from WSL; they have
   different processes, PATH and configuration. On Git Bash, use shell paths for shell tools and
   `C:/...` paths for native Node/Python/PowerShell when MSYS argument conversion is disabled.
   Convert a native path with `cygpath -u` before inserting it into Bash's colon-delimited PATH.
   Check `node --version` there. The separate aify-dashboard package requires Node 24.15 or later
   where it is built or run; Env's Node 20 minimum does not check that requirement. Read `npm prefix -g`
   with the same npm the installer will use. Put npm's global bin directory on PATH before installing:
   `<prefix>/bin` on Linux, WSL and macOS, or `<prefix>` on native Windows. The selected npm must
   agree with the `aify-env` and Wrapper commands already on PATH. A writable prefix alone does not
   make its commands reachable or make a different install safe to replace.
2. Run `bash scripts/install-state.sh --json` from the comms checkout. It reads endpoint, hooks,
   credential presence and app locations, and makes bounded service health queries. It never
   installs anything or starts a daemon. It may call `hermes config path` through the existing
   installer resolver. `HERMES_HOME` selects the intended profile; resolve uncertainty before writes.
3. Use `bash scripts/components.sh` for the required-component inventory, then each installed
   product's doctor for version and identity. Read launcher markers and installed package metadata,
   not just the checkout's VERSION. Do not run launchers to identify them. Missing commands and
   unreadable metadata are incomplete evidence, not permission to overwrite a working installation.
4. Report a row per relevant component: role, installed path/version, running version/build identity,
   intended version, installed/missing/outdated/unknown verdict, and proposed action. Docker errors,
   authentication failures and unresolved hook roots stay unknown. `apiKey=none` is a successful
   local resolution with no key, not evidence that a remote service accepts unauthenticated traffic.
5. Infer existing roles/clients/endpoint from the report; ask only unresolved intent, missing clients,
   endpoint and credential gaps. Also ask whether to install/update optional herdr or skip it. If no
   app was found, ask about a portable/custom install before choosing a new location. Skipping herdr
   does not block comms, managed agents, or `aify-env attach`.
6. For LAN/browser access, ask whether to enable optional HTTPS and obtain the exact URL and client
   devices. Follow [HTTPS setup and limits](HTTPS.md). Certificate trust is per device; successful
   validation on the server's own Windows account does not establish trust on a phone or another PC.
   Preserve TLS verification and the chosen hostname. Treat CA imports and proxy changes as explicit
   selections, not prerequisites for a local agent-only installation.

If the operator asked for verify-only, stop after the report. If they asked for plan-only, add the
selected versions and exact commands with restart impact, but execute no changes. These are workflow
modes, not flags on comms `install.sh`. Its `--emit-wrappers` writes files and is not a plan mode.

## Apply selected components

Before updates, inspect each checkout's origin, branch and dirty state. Choose an explicit target
release/commit with the operator, record the old SHA, then update the selected checkout without
resetting local work. Compare against that saved SHA, not an assumed `HEAD@{1}`. A failed upstream
lookup means the latest version is unknown. Do not invent an update verdict.

- **Service host:** preserve `.env` and service configuration; run `./setup.sh` only on first setup.
  Before a database-bearing update, create and verify a backup with SQLite's online backup API or
  the documented stopped-service procedure. Copying only a live `.db` file can omit WAL contents.
  Keep the backup outside the active data volume and retain the previous source/image identity.
  For a selected service update, `bash scripts/stamp.sh` then `docker compose up -d --build` after
  separate approval for the service interruption. Service runtime includes `service/`, `mcp/` outside
  `mcp/stdio/`, and `config/`. A healthy container alone is no reason to skip an outdated build.
- **Client host:** use `bash install.sh --client <claude|codex|hermes> <endpoint>` to enter the
  guided chain for each selected missing or outdated client. Use `--standalone` only for a deliberate
  Comms client-only install. An existing Claude or Codex notification hook is refreshed; adding a new
  one requires `--standalone --with-hook`. Hermes gets no hook, because it drops `post_tool_call`
  output: the installer removes the one an earlier install added. Read the relevant
  `install.<client>.md` for runtime-specific requirements.
  The guided chain requires aify-env's skills handoff v1 and a service build that advertises
  `comms-skills`. Env places the approved package's skills before the Comms assets step. The
  installer refuses an older handoff before it resolves the service key. Standalone creates
  missing skill directories and leaves existing ones alone; it reports and preserves anything
  different, linked or unreadable. Re-running standalone does not upgrade those skill trees.
- **Environment tier:** follow the reviewed [aify-env checkout](https://github.com/zimdin12/aify-env)
  README and `install.sh`. Register the selected service through the comms client installer first,
  so env can check its registered-service credentials. In the aify-env checkout:

  ```bash
  bash install.sh --plan-only   # reports the plan and credential gaps; installs nothing
  bash install.sh --no-prompt   # apply only after selection; fails on missing credentials
  ```

  These flags belong to aify-env, not comms. With a human terminal, `bash install.sh` prompts for
  missing credentials. A plan exit of zero is not proof that credentials exist: the reviewed env
  plan path reports credential-check errors but still exits zero. A no-prompt install can update
  packages before discovering a missing credential; report partial completion, not success.
  Read env's credential helper instructions to supply missing values privately, then rerun its
  installer. Preserve existing credentials, never echo them into reports or create a replacement key
  just because a resolver failed. Bare npm installs bypass this credential step.

No installer completion message authorizes starting or restarting aify-env. A second instance can
supersede the first and reap its workers. Get separate approval for host starts/restarts, service
rebuilds, wrapper relaunches and herdr launches. Identify affected sessions and offer deferral.

## Migrating skills copied by an older installer

The older installer copied the `aify-comms`, `aify-comms-debug`, and `aify-comms-install` directories
without recording ownership in aify-env. The guided install will not take over an existing tree
without a matching Env placement record, even if the files are identical. An unchanged tree that Env
placed can be updated by a guided re-run.

If Env names conflicting trees, **the operator moves each named directory to a backup outside every
skill search root, then re-runs the guided install for the affected client.** Move the directories;
do not delete them or edit the placement records. Keep unrelated skills in place. The paths in the
refusal reflect the selected profile. Default roots for older copies were:

- Claude: `~/.claude/skills/`
- Codex: `${CODEX_HOME:-~/.codex}/skills/`
- Hermes: `${HERMES_HOME:-~/.hermes}/skills/autonomous-ai-agents/`

`--standalone` only creates missing directories. It will not replace the conflicting copies.

## Optional herdr through its own installer

Upstream is **https://github.com/herdrdev/herdr**, not a similarly named npm package. Source reviewed
for this guide is tag **v0.9.0**, including `README.md`, `distribution/install.sh`,
`distribution/install.ps1`, and `src/main.rs`. This is a verified reference version, not a permanent
claim about the latest release. Recheck the official release/manifest before an actual installation.

Discovery and execution are separate:

```bash
bash scripts/herdr-state.sh          # files only, JSON
bash scripts/herdr-state.sh --probe  # explicit bounded --version, after checking the executable origin
```

`installed` records a discovered executable file, not full package integrity. `runnable` is unknown
until the opt-in version probe succeeds or fails; a timeout stays unknown. `version` is unknown
unless the successful output identifies herdr's version. `onPath` describes this shell only.
No hit in known locations is unknown because portable and package manager installs can live
elsewhere. With `HERDR_INSTALL_DIR` set, missing is scoped to that configured directory, never to
all applications on the host. Inspect a broken install before replacing it.

- **Linux/macOS:** download https://herdr.dev/install.sh to a local file, read it, then run that
  reviewed file with `sh` after opt-in. The official script selects OS/architecture from the release
  manifest and verifies SHA-256 before replacing `herdr` in `${HERDR_INSTALL_DIR:-$HOME/.local/bin}`.
  If Homebrew/mise/Nix owns the installed copy, follow that package manager's documented update path
  rather than installing a second copy. Direct installs support `herdr update`; review its impact
  and channel first, and do not run it during verify-only or plan-only.
- **Native Windows:** use herdr's PowerShell installer, not its Linux/macOS shell script under Git
  Bash. Download https://herdr.dev/install.ps1 and review before execution. If that endpoint is blocked,
  the same official source is
  https://raw.githubusercontent.com/herdrdev/herdr/v0.9.0/distribution/install.ps1.
  In PowerShell, after review and approval, run `& .\install.ps1 -Channel stable` from the downloaded
  script's directory. For an existing preview install, preserve its channel unless a switch was
  selected. Respect enterprise execution-policy/security restrictions rather than bypassing them.
  The Windows stable v0.9.0 asset is `herdr-windows-x86_64.zip`, not a bare exe. Preserve its bundled
  ConPTY runtime. The installer verifies the manifest SHA-256 and package completeness, stores
  versioned releases under `%USERPROFILE%\.herdr\packages\standalone\releases`, and points
  `%LOCALAPPDATA%\Programs\Herdr\bin` plus `current` at the active release. `HERDR_HOME` and
  `HERDR_INSTALL_DIR` are official overrides. It updates PATH; an already-open shell may still resolve
  an older copy. A pinned installer script still fetches a mutable release manifest: record the
  actual selected manifest version and asset digest, or use the official local-package options
  documented in that script for an approved, verified pinned archive.

After installation/update, inspect the resolved executable, run its bounded `--version`, and compare
with the approved version. Reopen/refresh the shell only as needed to observe PATH. Version success
alone does not prove ConPTY, a running herdr server, or env integration works.

## Integration support and final verification

The target command is `aify-env herdr`. It is a launch, not a check. Support in the installed env
build is **not guaranteed** by either herdr's presence or this guide. Inspect that build's source or
its documented safe help for the subcommand; an unknown-command exit means unsupported, not missing
herdr. Follow aify-env's guide for the integration and require its real named-pipe/socket, attach and
resize probe evidence before calling it operational. Stage 0 in the linked plan gates full integration.
Do not promise locked panes, status/sidebar features or lifecycle reconciliation without proof.

Finish with `aify-comms doctor` and `aify-env doctor` on the selected host. Match the service's running
build SHA with the selected source, compare installed bridge/package/launcher fingerprints, and
report still-running old workers separately. Check registry and credential status without printing
secret values. With approval for a disposable smoke test, verify managed spawn/attach against the
selected endpoint. If that test or the herdr integration probe was not run, state the gap. Report
installed/updated, verified, skipped, unknown and pending disruptive actions separately.
