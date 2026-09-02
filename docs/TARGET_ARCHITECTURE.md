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
one is `npm install -g github:zimdin12/aify-env` plus aify-wrapper's
`install.sh --all --endpoint <url>` -- both of which already exist and already work.

**The git form, not the bare name.** Neither package is published to npm: `npm install -g aify-env`
returns a 404, and four documents carried it as the client-path instruction. It worked on the machine
it was written on because aify-env is `npm link`ed there, which is the shape of this failure -- an
install command verified by the one person who never has to run it. Publishing is the operator's
call, since it leaves the machine; until then the `github:` form is the one that resolves, and it
does: `npm view github:zimdin12/aify-env version` answers 0.6.0.

## Commands on PATH

```
aify-env            the host tier, with its doctor as a SUBCOMMAND and a TUI
claude-aify         launcher
codex-aify          launcher
hermes-aify         launcher
```

Nothing else. No `aify-comms` command, no `aify-doctor`, no `aify-env-doctor` as a second binary.

**Measured on this host, 2026-08-24, after installing all three layers at 0.6.0.** Eight, not four,
and the gap is worth naming rather than leaving a list the machine visibly contradicts:

| on PATH | why | goes when |
|---|---|---|
| `aify-env`, `claude-aify`, `codex-aify`, `hermes-aify` | the target four | — |
| `aify-comms` | the environment bridge. It is the only thing that CLAIMS a spawn -- hosting moved to aify-env on 2026-08-25, claiming did not | aify-env's comms plugin is proven on real hardware (open item 2). "Phase 8 flips" was the old condition and it read as met for eight days while this command stayed load-bearing |
| `aify-wrapper-check`, `aify-wrapper-install` | aify-wrapper's own commands, installed by the client path by construction. A launcher answering for itself needs a command to ask | they are the client path; the list above should include them |
| `aify-doctor` | an alias for `aify-comms doctor`, kept for agent habits and older docs | **the only genuine leftover.** One line in install.sh, and the question is whether anything still reaches for the old name |

Three of the four extras are structural and one is a habit. `aify-env-doctor` is genuinely gone.

## Where each doctor lives

- **aify-comms' doctor runs inside the container.** It answers about the container: its build, its
  registrations, its reachability, its quota. It is reached from a container terminal or over HTTP.
- **aify-env's doctor is `aify-env doctor`**, plus the TUI. It answers about the host — PTYs,
  processes, the registry — and it **relays** what each registered service said about itself.
- **A launcher only REPORTS its state.** It does not host a doctor. Its version and the registry it was
  built against travel to the service, and the service answers questions about them.

Nothing inspects another component's internals. That rule is older than this document and is argued in
[AIFY_ENV_BOUNDARY.md](AIFY_ENV_BOUNDARY.md); this file only records where the pieces end up.

## What is in the way — re-measured 2026-08-24, after a day of work

Four of the five items below were closed. What remains is one flip and one deletion, both gated on the
operator rather than on effort.

**Closed:**

- ~~A launcher does not report its version.~~ All four templates now export `HARNESS_WRAPPER_VERSION`
  and `HARNESS_REGISTRY_FINGERPRINT`, and the bridge sends them at registration as `launcherVersion`
  and `launcherRegistryFingerprint`. A launcher REPORTS its state; it does not host a doctor.
- ~~`aify-comms doctor` answers other tiers' questions.~~ Twelve checks are eight. `wrappers`,
  `wrapper-current` and `runtimes` went to aify-wrapper, where `aify-wrapper-check` already
  implemented them; `bridge-terminal` went to `aify-env doctor`. Verified on a live host: both still
  answer.
- ~~Three binaries for one product.~~ `aify-env doctor` and `aify-env tui` are subcommands, and an
  unknown one exits 64 rather than falling through to starting the daemon.
- ~~The container's MCP transport does not load.~~ `mcp[cli]` was unbounded above, floated to 2.0.0
  and lost the API `sse_server.py` imports; the failure logged at INFO so it read as "not configured".
  Bounded to `<2`, logged as a WARNING, tested. `/mcp/sse` returns 200.

**Left, and both are the operator's call, not a missing capability:**

1. **The client path still installs aify-comms code.** `--mcp-transport sse` exists and renders a
   launcher that talks to `<endpoint>/mcp/sse` instead of spawning a local bridge. Proven end to end
   rather than by status code: the container completes the MCP handshake, issues a session id, and
   serves 22 `comms_*` tools to a client that asks for them. `~/.aify-comms` stops being load-bearing
   the moment every launcher on a host uses it.

   **But it is not a free swap, and saying "one install flag" understated it.** The SSE surface is
   REDUCED by design and `mcp/stdio/tests/transport-parity.test.js` requires every difference to be
   declared. Nine of the fourteen missing tools are principled — `comms_spawn`, `comms_restart`,
   `comms_compact`, `comms_interrupt`, `comms_delete_session`, `comms_remove_agent` need a local
   process; `comms_usage`, `comms_envs`, `comms_listen` read host state a container cannot see. The
   other five are absent only because nobody mirrored them: `comms_agent_info`, `comms_contracts`,
   `comms_status`, `comms_describe`, `comms_unsend`. The parity gate already flags `comms_agent_info`
   as the one worth revisiting, since it is where an agent's production is reported.

   So an agent moved to SSE today loses lifecycle control it could not have had anyway, and five tools
   it could. Mirroring those five is the work that makes this flag a genuine equivalence rather than a
   trade, and it is ordinary work, not a decision.

2. **The `aify-comms` command still CLAIMS managed spawns.** Hosting moved on 2026-08-25; claiming
   did not, and this entry read as finished for eight days because it recorded the half that moved.

   **The correction, 2026-09-02.** `/spawn` requires `metadata.bridgeLastSeen`, written only for a
   heartbeat carrying a `bridgeId`, and only the environment bridge sent one. So the command stayed
   load-bearing while the table above said it "goes when Phase 8 flips" and this entry said Phase 8
   had flipped. The operator ran everything they were told to and was refused six times; two agents
   read this document and concluded the fleet was ready. **A document that claims completion is how a
   thing stops being checked** -- and the claim here was true of execution and false of claiming.

   **Where it now stands:** aify-env carries an `aify-comms` PLUGIN that heartbeats with a `bridgeId`
   and claims spawn requests, proven end to end against a real socket, a real `Runner` and a real
   process. Nothing in the DELIVERY path needs the command either -- the launcher spawns its own
   delivery loop (`hermes-aify:562`), which the bridge never did. What remains before the command can
   be deleted is proving it on real hardware: install, restart, and one spawn with no bridge running.

   ~~**DONE 2026-08-25: delegation is ON.**~~ (Kept, struck through, because the wrong claim is the
   point: it is what a reader believed for eight days.)
   The operator took the call on an idle fleet, `install.sh --delegate-spawns` bakes it into the
   environment-bridge launcher, and `aify-comms doctor`'s `spawn-delegation` reports the setting and
   whether aify-env is answering.

   **Proven end to end rather than by reading**, through the same code the bridge runs: a real
   `claude-aify` launched by aify-env, exit 0, its output streamed back, and aify-env owning the
   process while it lived. That proof found three defects nothing else had — argv never reaching the
   spawn, Windows resolving the launcher to a `.cmd` shim aify-env refuses, and the launcher path
   losing every backslash before bash. Each would have broken the first spawn with every component
   looking healthy.

   What remains of this item is the command itself: `aify-comms` still exists as the environment
   bridge, and deleting it is the last step rather than the flip.

When both are on, PATH holds `aify-env` and the three launchers, and nothing else.

## The client path, verified from a clean install

Documented install commands rot in a particular way: they are written on the machine that already has
the thing, and verified by the person who never has to run them. `npm install -g aify-env` sat in four
documents returning 404 for everyone else, correct-looking because aify-env is `npm link`ed here.

So this was run rather than reasoned about, into a throwaway npm prefix so the real install was never
touched:

```
npm install -g --prefix <tmp> github:zimdin12/aify-wrapper
  -> aify-wrapper-check, aify-wrapper-install (+ .cmd and .ps1 shims), VERSION 0.6.0
<tmp>/aify-wrapper-check   -> ok claude-aify / ok codex-aify / ok hermes-aify, "3 current"
<tmp>/aify-wrapper-install --help  -> usage, no install performed
```

Both bins execute on Windows, where a `.sh` entry behind a generated `.cmd` shim is the thing most
likely not to. `npm view github:zimdin12/aify-env version` answers 0.6.0 the same way.

Re-run this the next time either package's packaging changes. It is cheap, and it is the only check
that does not depend on this machine's own state.
