# The target shape, as the operator has specified it

This is not a proposal and not a discussion. It is the operator's stated target, written down because it
had to be repeated several times before anyone recorded it. Anything that disagrees with this document
is the thing that is wrong. Where the system has not reached the target yet, the gap is named under
"What is left".

## Three places, and what lives in each

| where | what it is | owns |
|---|---|---|
| **the container** | aify-comms | its own database, its own config, its own doctor. Reached over HTTP. |
| **the host** (windows / linux / mac) | `aify-env` and the `*-aify` launchers | processes, terminals, and launching runtimes |
| **`~/.aify`** | the shared config directory | read and written by aify-env and the launchers, on the host |

The container is a service. The host is where agents actually run. `~/.aify` is how the host-side
pieces agree with each other without a fourth component to coordinate them.

## Installing is TWO paths, not one

They are separate installs for separate roles, and a machine may do either, both, or neither.

| path | what you install | what you get |
|---|---|---|
| **backend / service** | the container | aify-comms: its database, its config, the dashboard, its own doctor. Reached over HTTP. |
| **client / frontend** | `aify-env` + `aify-wrapper` | the host tier and the `*-aify` launchers, configured through `~/.aify`, pointed at a service that may live on another machine |

Nothing in the client path is aify-comms code. That is the test for whether the split is real: a host
that runs agents installs aify-env and the launchers, and carries no copy of the service.

aify-env's installer (`./install.sh` in its repo) asks for a service credential the host is missing,
which a bare `npm install -g` cannot notice: a host installed without one advertises, is refused with
401, and reports healthy throughout.

**The git form, not the bare name.** Neither package is published to npm, so `npm install -g aify-env`
returns a 404. The `github:zimdin12/aify-env` and `github:zimdin12/aify-wrapper` forms are the ones that
resolve. Publishing is the operator's call, since it leaves the machine.

## Commands on PATH

```
aify-env            the host tier, with `doctor` and `tui` as subcommands
claude-aify         launcher
codex-aify          launcher
hermes-aify         launcher
herdr-aify          launcher for Herdr: a persistent Herdr for residents, or `herdr-aify env`
                    for an isolated Herdr with its own aify-env
aify-herdr-pane     the aify side of an ordinary Herdr (mostly invoked by the wrapper and Herdr's hook)
aify-wrapper-check  asks each installed launcher whether it is current
aify-wrapper-install
```

The last five are aify-wrapper's own commands, so they arrive with the client path by construction.
No `aify-comms` command, no `aify-doctor`, no `aify-env-doctor` as a second binary. An unknown
`aify-env` subcommand exits 64 rather than falling through to starting the daemon.

## Where each doctor lives

- **aify-comms' doctor runs inside the container.** It answers about the container: its build, its
  registrations, its reachability, its quota. It is reached from a container terminal or over HTTP.
- **aify-env's doctor is `aify-env doctor`**, plus the TUI. It answers about the host (PTYs,
  processes, the registry) and it **relays** what each registered service said about itself.
- **A launcher only REPORTS its state.** It does not host a doctor. Each launcher carries and exports
  `HARNESS_WRAPPER_VERSION` and `HARNESS_REGISTRY_FINGERPRINT`; `aify-wrapper-check` reads them to
  say whether a launcher is current, and aify-env reads the first to decide whether it may run the
  file at all.

Nothing inspects another component's internals. That rule is argued in
[AIFY_ENV_BOUNDARY.md](AIFY_ENV_BOUNDARY.md); this file only records where the pieces end up.

**Spawning is aify-env's alone.** Its `aify-comms` plugin claims spawn requests and terminal controls
from this service, runs the launcher as a file with structured `argv`, and streams the console back.
There is no second spawner and no fallback. Two spawners on one host is the collision the host tier
exists to end, so a retired aify-comms environment bridge still running somewhere never takes the
claimer role from aify-env. `aify-comms doctor`'s `spawn-delegation` says whether the aify-env serving
this host is answering.

## What is left

Three gaps between the system and the target.

1. **The client path still installs aify-comms code.** aify-comms' own `install.sh --client <runtime>`
   is today the client installer: it writes the service's entry into `~/.aify/services.json` and copies
   the MCP bridge into `~/.aify-comms`, which every launcher using the default stdio transport runs.
   `--mcp-transport sse` renders a launcher that talks to `<endpoint>/mcp/sse` instead, and the
   container serves the `comms_*` tools over it, so `~/.aify-comms` stops being load-bearing the
   moment every launcher on a host uses it.

   It is not a free swap. The SSE surface is reduced by design and
   `mcp/stdio/tests/transport-parity.test.js` requires every difference to be declared. Nine of the
   fourteen missing tools are principled: `comms_spawn`, `comms_restart`, `comms_compact`,
   `comms_interrupt`, `comms_delete_session`, `comms_remove_agent` need a local process;
   `comms_usage`, `comms_envs`, `comms_listen` read host state a container cannot see. The other five
   are absent only because nobody mirrored them: `comms_agent_info`, `comms_contracts`,
   `comms_status`, `comms_describe`, `comms_unsend`. Mirroring those five is ordinary work, not a
   decision.

2. **The verifier is still a host command.** `aify-comms` on PATH is a verifier and nothing else:
   `doctor`, `--check`, `--version`, `--help`; anything else exits 2 naming aify-env. `aify-doctor`
   is the same script under its older name. The target puts aify-comms' doctor inside the container,
   and moving it means giving the container a way to answer host questions it cannot see (the
   installed bridge copy and skills, the aify-env serving the host, running processes), so it is not a rename. Both names stay on
   PATH until that is answered.

3. **Launcher state does not reach the service.** The service accepts `launcherVersion` and
   `launcherRegistryFingerprint` on an environment heartbeat and shows them on the environment row,
   but the only sender was the environment bridge that v0.6.3 deleted, and aify-env does not send
   them. Until it does, the service cannot answer questions about a host's launchers;
   `aify-wrapper-check` on that host can.

When the first two are closed, PATH holds `aify-env`, the launchers and aify-wrapper's commands, and nothing
from aify-comms.

## The client path, verified from a clean install

Documented install commands rot in a particular way: they are written on the machine that already has
the thing, and verified by the person who never has to run them. So verify the client path into a
throwaway npm prefix, which never touches the real install:

```
npm install -g --prefix <tmp> github:zimdin12/aify-wrapper
<tmp>/aify-wrapper-check            # one line per installed launcher, then a summary
<tmp>/aify-wrapper-install --help   # usage, no install performed
npm view github:zimdin12/aify-env version
```

On Windows this is also the check that a `.sh` entry behind a generated `.cmd` shim runs at all. Re-run
it whenever either package's packaging changes; it is the only check that does not depend on this
machine's own state.
