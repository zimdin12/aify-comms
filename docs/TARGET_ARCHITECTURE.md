# The target shape, as the operator has specified it

This is not a proposal and not a discussion. It is the operator's stated target, written down because it
had to be repeated several times before anyone recorded it. Anything that disagrees with this document
is the thing that is wrong.

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

**Today it fails that test.** `install.sh` is one script doing both halves, and the client half installs
92 MB of aify-comms' own runtime into `~/.aify-comms`. Two paths means two installers, and the client
one is `npm install -g aify-env` plus aify-wrapper's `install.sh --all --endpoint <url>` -- both of
which already exist and already work.

## Commands on PATH

```
aify-env            the host tier, with its doctor as a SUBCOMMAND and a TUI
claude-aify         launcher
codex-aify          launcher
hermes-aify         launcher
```

Nothing else. No `aify-comms` command, no `aify-doctor`, no `aify-env-doctor` as a second binary.

## Where each doctor lives

- **aify-comms' doctor runs inside the container.** It answers about the container: its build, its
  registrations, its reachability, its quota. It is reached from a container terminal or over HTTP.
- **aify-env's doctor is `aify-env doctor`**, plus the TUI. It answers about the host — PTYs,
  processes, the registry — and it **relays** what each registered service said about itself.
- **A launcher only REPORTS its state.** It does not host a doctor. Its version and the registry it was
  built against travel to the service, and the service answers questions about them.

Nothing inspects another component's internals. That rule is older than this document and is argued in
[AIFY_ENV_BOUNDARY.md](AIFY_ENV_BOUNDARY.md); this file only records where the pieces end up.

## What is in the way, measured 2026-08-23

Recorded so the gap is a work list rather than a rediscovery.

1. **The client path installs aify-comms code.** `~/.aify-comms` is 92 MB and 160 host-side JS files. It is aify-comms' bridge runtime
   copied onto the host, because a coding agent loads a *stdio* MCP server locally instead of talking
   to the container. In the target shape this directory does not exist.

2. **The container's MCP transport does not load.** `mcp/sse_server.py` ships and `service/main.py`
   mounts it, but startup logs `MCP SSE server not available: No module named 'mcp.server.fastmcp'`, so
   `/mcp/sse` is a 404. This is the single blocker for point 1: until agents can reach MCP over HTTP,
   the host copy is load-bearing.

3. **A launcher does not report its version.** `HARNESS_WRAPPER_VERSION` is a shell local, never
   exported, so the only way to know which launcher started a session is to read the file on the host.
   That is why launcher staleness is a host command today.

4. **`aify-comms doctor` answers other tiers' questions.** Of its twelve checks, three belong to
   aify-wrapper (which already implements them in `aify-wrapper-check`) and one duplicates aify-env's
   terminal check. Four more are about `~/.aify-comms` and stop existing with point 1.

5. **The `aify-comms` command still hosts managed agents.** It can only disappear once spawning goes
   through aify-env — built, and off behind two environment variables.
