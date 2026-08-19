# aify-env: what moves, what stays, and why the allowlist writes itself

Design capture, 2026-08-19. The operator's shape, my measurements, and the two consequences that
follow from it rather than from me. Nothing here is built. It exists so the decisions survive the
conversation they were made in.

Companion to [MULTI_SERVICE_STACK_TRACE.md](MULTI_SERVICE_STACK_TRACE.md), which established that
per-service endpoints already work and that the real blocker is spawning living inside aify-comms.
This is the answer to that blocker.

---

## Four components, one config

| | Owns | Knows about |
|---|---|---|
| **aify-wrapper** | The four launchers. Installs one per harness present. | Harnesses. Not services. |
| **aify-env** | Processes and PTYs on this host. One per host. Hosts `aify-doctor`. | Neither. Runs what it is told, from the allowlist below. |
| **aify-comms** | Messaging, dispatch, channels, agent semantics. | Agents. Stops being a command. |
| **aify-dashboard** | Agent-pushed HTML, liveness pages, tasks, docs, projects. | Reads the others. |

Both aify-wrapper and aify-env read **the same config**, `~/.aify/services.json`, and connect to the
same registered services. One file, two readers, no second source of truth.

`aify-comms` as a COMMAND goes away. Today `install_bridge_launcher()` (`install.sh:1285`) writes
`~/.local/bin/aify-comms` beside `claude-aify`, `codex-aify`, `hermes-aify` and `pi-aify` — four
harness launchers and one environment bridge, same directory, same shape, entirely different job, and
the BARE invocation is the destructive one. That is the exact mechanism of the 2026-08-11 incident:
a four-second run meant only to confirm the launcher still started superseded the live environment
bridge and reaped nine managed agents. Renaming it is not cosmetic. Removing the collision beats
renaming around it, so the string only ever names the service.

## The cut, measured

`terminal-runtime.js` (691 lines) already holds both execution paths, and neither knows what claude is:

```js
const term = pty.spawn(shell, args, {…})   // PTY — the dashboard console needs a real TUI
const proc = spawn(command, {…})            // piped stdio when node-pty is unavailable
```

**That is aify-env.** Beside it sit the files that are not:

    claude-console-prompts.js        claude-stop-gate.js
    claude-turn-detector-state.mjs   hermes-gateway-turn-detector.js
    hermes-turn-detector-callbacks.mjs

Turn detection, stop gating, steering. Hermes' `prompt.submit` interrupts where `session.steer`
queues, and getting that backwards was a real bug here. **None of it is running a command.** It is
knowing when an agent stopped thinking.

Roughly one generic file against ~16.9k lines of harness semantics in `mcp/stdio` (split by filename,
so an order of magnitude rather than a figure). The risk is not the cut, it is the 16.9k following the
691 into the new repo, at which point aify-env is the bridge with a new name in a new place.

**What aify-env fixes:** two PTY owners on one host, which was the whole of Finding 4. One owner,
every service asking it, collision gone by construction.

**What it does not fix:** status. A dashboard asking aify-env "is this agent working?" gets nothing
useful — aify-env knows the process is alive, not whether the agent is thinking. Worth being explicit
now, because "aify-env tracks the agents" is the natural assumption and it is wrong.

## The allowlist writes itself

A host service that runs commands in the background, reachable by any registered service, is remote
code execution by design. Today the environment bridge only launches wrappers it was told to spawn;
that constraint is incidental, not enforced. Generalise it and the constraint is gone.

The fix is already in the artifacts. Every contract wrapper carries a marker on a line of its own:

    wrappers/claude-aify.sh.in:46   HARNESS_WRAPPER_VERSION="@@WRAPPER_VERSION@@"
    wrappers/codex-aify.sh.in:27    HARNESS_WRAPPER_VERSION="@@WRAPPER_VERSION@@"
    wrappers/hermes-aify.sh.in:24   HARNESS_WRAPPER_VERSION="@@WRAPPER_VERSION@@"
    wrappers/pi-aify.sh.in:26       HARNESS_WRAPPER_VERSION="@@WRAPPER_VERSION@@"

**aify-env executes a file only if that file carries the marker.** Derived from the artifact, not from
a list — which is this repo's own rule, that a list you must remember to update is a defect with a
delay on it. Installing a wrapper enrols it, nobody edits a policy file, and a new harness is
automatic.

Doctor already proves the read is safe: it takes `HARNESS_WRAPPER_VERSION` out of the file rather than
running `--check`, precisely because asking a pre-contract wrapper its version would LAUNCH CLAUDE.
The same reasoning applies to an allowlist — inspect the artifact, never run it to decide whether to
run it.

Open: whether the marker alone is enough, or whether the installed set should also be recorded at
install time so that a hand-written file carrying the marker cannot enrol itself. For a local trust
boundary the marker is probably enough; for a shared host it is not.

## aify-doctor moves into aify-env, and becomes a plugin host

Every check has an obvious owner once the tiers exist, and no tier can answer another's:

| check | owner |
|---|---|
| `env-bridge`, `bridge-terminal` | **aify-env** — is an environment online, does node-pty load |
| `wrappers`, `runtimes`, `wrapper-current` | **aify-wrapper** |
| `service`, `bridge-installed`, `bridge-running`, `bridge-current`, `agent-identity`, `usage-openai` | **aify-comms** |

So doctor cannot stay a monolith across a four-repo split: half its checks would be asking about a
repo it no longer ships with. aify-env owns the framework, the report shape (`{ok, checks:[{id, ok,
code, detail, fix}]}`), `--json` and `--strict`; each registered service contributes its own checks,
discovered through the same `services.json` both other components already read.

This keeps the property that matters most about doctor, which is that it proves claims against the
running system rather than checking that a file exists — and the rule that no evidence is not a pass.
A service that is registered but cannot be reached for its checks reports `unknown`, never `ok`.

## Open questions

1. **Name.** `aify-env` names the tier; `aify-comms-bridge` names today's coupling and would need
   renaming again once the tier is shared. Operator's call.
2. **The request contract** between a service and aify-env. Not designed. It is the piece that decides
   whether the 16.9k lines can stay put.
3. **Shared-host trust**, per the allowlist note above.
4. **aify-wrapper installs one launcher per `--client` and does not detect what is present.** The
   operator's requirement is that installing it installs a launcher for every harness in the
   environment. Small, and independent of everything else here.
