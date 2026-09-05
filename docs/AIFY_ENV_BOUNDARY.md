# aify-env: what moves, what stays, and why the allowlist writes itself

Design capture, 2026-08-19. **Built and published since:** <https://github.com/zimdin12/aify-env>.
Where this document and the code disagree, the code is the answer — this records the reasoning,
not the state. The operator's shape, my measurements, and the two consequences that
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
| **aify-env** | Processes and PTYs on this host. One per host. Answers `aify-env doctor`. | Neither. Runs what it is told, from the allowlist below. |
| **aify-comms** | Messaging, dispatch, channels, agent semantics. | Agents. Stops being a command. |
| **aify-dashboard** | Agent-pushed HTML, liveness pages, tasks, docs, projects. | Reads the others. |

Both aify-wrapper and aify-env read **the same config**, `~/.aify/services.json`, and connect to the
same registered services. One file, two readers, no second source of truth.

`aify-comms` as a BRIDGE is gone, v0.6.1. `install_bridge_launcher()` wrote `~/.local/bin/aify-comms`
beside `claude-aify`, `codex-aify`, `hermes-aify` and `pi-aify` — four harness launchers and one
environment bridge, same directory, same shape, entirely different job, and the BARE invocation was
the destructive one. That is the exact mechanism of the 2026-08-11 incident: a four-second run meant
only to confirm the launcher still started superseded the live environment bridge and reaped nine
managed agents. Renaming it would not have been cosmetic; removing the collision beat renaming
around it.

What that function writes now is a verifier — `doctor`, `--check`, `--version`, `--help` — and a bare
run exits 2 naming aify-env. **The collision is what went, not the name**: the file is still in that
directory and still shaped like a launcher, so the remaining question is where the DOCTOR belongs,
not which process the string starts. Nothing it can do now is destructive.

## The cut, measured

**THE CUT IS COMPLETE AS OF 2026-09-05**: `terminal-runtime.js` and the files named beside it were
deleted from this repo, because the tier they belonged to is aify-env's. The argument below is kept
as the RECORD OF WHY the line was drawn where it was -- it reads in the present tense because it was
written before the move.

`terminal-runtime.js` already holds both execution paths, and neither knows what claude is:

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

Roughly one generic file against ~16.9k lines of harness semantics in `mcp/stdio` — measured
2026-08-19 by filename, so an order of magnitude rather than a figure, and it rots: re-measure before
quoting it. The risk is not the cut, it is the 16.9k following the
691 into the new repo, at which point aify-env is the bridge with a new name in a new place.

**What aify-env fixes:** two PTY owners on one host, which was the whole of Finding 4. One owner,
every service asking it, collision gone by construction.

**What it does not fix:** status. A dashboard asking aify-env "is this agent working?" gets nothing
useful — aify-env knows the process is alive, not whether the agent is thinking. Worth being explicit
now, because "aify-env tracks the agents" is the natural assumption and it is wrong.

## The allowlist writes itself

A host service that runs commands in the background, reachable by any registered service, is remote
code execution by design. aify-env only launches wrappers it was told to spawn; that constraint is
incidental, not enforced. Generalise it and the constraint is gone — and generalising it is exactly
what aify-dashboard and aify-project-graph will ask for, since both consume this tier.

The fix is already in the artifacts. Every contract wrapper carries a marker on a line of its own:

    aify-wrapper's claude-aify.sh.in   HARNESS_WRAPPER_VERSION="@@WRAPPER_VERSION@@"
    aify-wrapper's codex-aify.sh.in    HARNESS_WRAPPER_VERSION="@@WRAPPER_VERSION@@"
    aify-wrapper's hermes-aify.sh.in   HARNESS_WRAPPER_VERSION="@@WRAPPER_VERSION@@"
    aify-wrapper's pi-aify.sh.in       HARNESS_WRAPPER_VERSION="@@WRAPPER_VERSION@@"

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

## The environment's doctor lives in aify-env, and ASKS rather than inspects

An earlier draft of this file had aify-env hosting a doctor that ran every component's checks as
plugins. The operator rejected it, correctly: that is still centralisation, just with a nicer name. A
component that inspects another component's internals has taken on that component's concern.

**Each component answers only questions about itself.**

| | answers about itself |
|---|---|
| **aify-wrapper** | are the launchers installed, are they current, do the runtime CLIs exist |
| **aify-env** | am I running, does node-pty load, which services are registered and answering, which processes do I own |
| **aify-comms** | is my container serving the build I think it is, is my bridge current, do my registered agents carry identity, does my quota token still work |

Nothing crosses that line except **reachability**, which is symmetric and honest: aify-env can say
"aify-comms is registered and answering" or "registered and silent". It cannot say "aify-comms is
healthy" — it asks, and displays the answer it was given.

So `aify-env doctor` is a **collector and a display**, not an inspector. It runs its own environment
checks, asks each registered service for its self-report, and renders both.

**It was called `aify-doctor` until 2026-08-20, and that was the boundary being crossed in the command
name.** aify-comms installs a different tool under exactly that name -- its deploy verifier, which
inspects containers and bridges. Two tiers on one host is what this document is for, so `npm install
-g` would have shadowed one with the other, silently, and whichever won would look like it had changed
its mind about what it reports. Every command aify-env puts on PATH is now named after the package,
and a test derives that rule rather than listing other projects' names.

Two constraints make a self-report trustworthy, and both come from failures this project already had:

- **A self-reported build value must be an observation of a build, never configuration.**
  `config/service.json` was a second way to set `build_sha` until 2026-08-18, which meant a hand-edit
  could make the one stale-deploy instrument agree with a sha nothing was ever built from. The
  stamp-owned fields are now refused from that file. Any service self-reporting its build inherits
  that rule or inherits that bug.
- **Comparing a reported sha against a checkout is a DEVELOPER action, not a runtime one.** A running
  service has no repo — that is why the sha is stamped in the first place. So the service reports what
  it is, and the comparison against HEAD lives in that service's own repo tooling, where a checkout
  exists.

And the rule that survives everywhere: **unanswered is not a pass.** Today `skip()` pushes
`ok: true`, and `--strict` exits on `failed.length`, so on Windows a green strict run means ten
verified and two unanswerable rather than twelve verified. That is survivable at twelve checks on one
host. Across four components where "service not installed" and "service silent" become ordinary, it
stops being survivable. **passed / failed / unanswered**, with unanswered visible and carrying its own
exit status, is a prerequisite of the split rather than a later polish.

## The TUI, and the limit on what it may claim

aify-env is visible, not just a daemon. It can honestly show:

- **Registered services** from `services.json` — reachable or silent, plus whatever each self-reports.
- **Processes it owns** — pid, wrapper, cwd, uptime. Ground truth, because it started them.
- **Traffic through itself** — spawn requests, output bytes. Its own I/O, which it genuinely observes,
  which is what an activity animation may be driven by.

The limit: aify-env knows **processes**, not agents. Alive is not the same as working. A managed-agent
list may show what aify-env owns, annotated with what aify-comms reports when asked, and must not
derive status of its own — deriving it in two places is how two answers start disagreeing.

## Open questions

1. **Name.** `aify-env` names the tier; `aify-comms-bridge` names today's coupling and would need
   renaming again once the tier is shared. Operator's call.
2. **The request contract** between a service and aify-env. Not designed. It is the piece that decides
   whether the 16.9k lines can stay put.
3. **Shared-host trust**, per the allowlist note above.
4. **aify-wrapper installs one launcher per `--client` and does not detect what is present.** The
   operator's requirement is that installing it installs a launcher for every harness in the
   environment. Small, and independent of everything else here.
