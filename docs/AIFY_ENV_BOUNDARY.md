# aify-env: what moves, what stays, and why the allowlist writes itself

Design capture, 2026-08-19, since built: <https://github.com/zimdin12/aify-env>. Where this document
and the code disagree, the code is the answer: this records the reasoning behind the boundary, so the
decisions survive the conversation they were made in.

Companion to [MULTI_SERVICE_STACK_TRACE.md](MULTI_SERVICE_STACK_TRACE.md), which established that
per-service endpoints already work and that the real blocker is spawning living inside aify-comms.
This is the answer to that blocker.

---

## Four components, one config

| | Owns | Knows about |
|---|---|---|
| **aify-wrapper** | The four launchers. Installs one per harness present. | Harnesses. Not services. |
| **aify-env** | Processes and PTYs on this host. One per host. Answers `aify-env doctor`. | Neither, in its HOST tier. Its service PLUGINS know their own service — see the carve-out below. |
| **aify-comms** | Messaging, dispatch, channels, agent semantics. | Agents. On a host it is only a verifier command that starts nothing. |
| **aify-dashboard** | Agent-pushed HTML, liveness pages, tasks, docs, projects. | Reads the others. |

Both aify-wrapper and aify-env read **the same config**, `~/.aify/services.json`, and connect to the
same registered services. One file, two readers, no second source of truth.

### The plugin carve-out, and the line it does NOT move — 2026-09-08

"Neither" was flatly true until v0.6.3, and the operator asked for a feature that appears to break it:
*"spawn, start available agent (mb get available via aify-comms plugin that aify-env has?)"*. aify-env's
view now lists agents and can ask for one to be started. That IS service knowledge, and their own
parenthesis names the mechanism that makes it safe.

**THE RULE: a SERVICE PLUGIN may know its service. The HOST tier may not, and that includes what it
prints.** The plugin holds the endpoint, the credential, the roster and the vocabulary; the daemon
reaches it by CAPABILITY name (`capability("agents")`) and never by service name, so a second `aify-`
service offering the same capability needs no change in the host at all.

**MEASURED ON THE SHIPPED CODE**, because a rule with no measurement is prose: every occurrence of
`aify-comms` in `bin/aify-env.mjs`, `lib/protocol.mjs`, `lib/service-plugins.mjs`, `lib/keys.mjs`,
`lib/console-session.mjs`, `lib/daemon-view.mjs`, `lib/startable-agents.mjs` and `lib/tui.mjs` is a
COMMENT. Exactly one was not, and it was a defect: the renderer printed `asking aify-comms…` while a
list loaded. That string would have been wrong the day a second service offered the capability, and
wrong in the most confusing direction — naming the service that is *not* the one failing to answer.
The name now travels **with the answer**, from the plugin that knows it, through the route, to a view
that renders whatever it was told and says `asking…` when it was told nothing.

**WHAT THIS DOES NOT LICENCE.** The earlier draft of `dashboard.mjs` fetched each service's AGENT LIST
to *display*, and that was reverted on the operator's ruling — *"aify-env should not ask stuff from
aify-comms, there should not be requirement, it is not aify-env's concern."* That ruling stands. The
difference is not "agents are allowed now": it is that this list exists to **perform an action the
operator asked for**, it lives behind a plugin, and the host degrades to a plain refusal with no plugin
present. A panel that merely *shows* somebody else's domain data is still the wrong side of the line.

### Screen knowledge — amended 2026-09-14

**THE RULE, as the operator amended it:** aify-env's host core stays PTY-only. It exposes screen
text and PTY activity, never what a runtime's screen means. A service's screen knowledge may live in
that service's own plugin directory, `lib/plugins/<service>/`, because the plugin runs next to the
authoritative screen. The 2026-09-03 ruling is amended, not overturned: it had moved a claude screen
model out of aify-env's core after it was added there to unblock a fleet, and the core still may not
carry one.

What follows from it today: aify-comms' plugin vendors Herdr's per-runtime screen rules and reports
working / idle / blocked for the managed terminals it runs; this service stores the report
(`service/api_core/host_activity.py`) and uses a fresh one for a managed agent's status. The
service's own screen code stays as it is for now: `console_prompts.py` answers dialogs, and the
`console_working.py` footer lease is the fallback for an aify-env that sends no observation. The
lease can be retired once every supported aify-env sends one.

### `aify-comms` on PATH is a verifier

Until v0.6.1, `install_bridge_launcher()` wrote `~/.local/bin/aify-comms` beside the harness
launchers, same directory, same shape, and a BARE run started an environment bridge that superseded
the live one and reaped its managed agents (nine of them on 2026-08-11). What that function writes now
is a verifier: `doctor`, `--check`, `--version`, `--help`; anything else exits 2 naming aify-env.
**The collision is what went, not the name**: the remaining question is where the doctor belongs
(see [TARGET_ARCHITECTURE.md](TARGET_ARCHITECTURE.md), "What is left"), and nothing the command can
do is destructive.

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
now, because "aify-env tracks the agents" is the natural assumption and it is wrong. (Since
2026-09-14 the aify-comms PLUGIN reports what an agent's screen shows to this service, which decides
the status; aify-env's host core still answers nothing about it. See the screen-knowledge section
under the plugin carve-out.)

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

This is what aify-env's allowlist does, and `aify-wrapper-check` reads the same marker out of the file
rather than running `--check`, because asking a pre-contract wrapper its version would LAUNCH CLAUDE.
Inspect the artifact; never run it to decide whether to run it.

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

And the rule that survives everywhere: **unanswered is not a pass.** `aify-env doctor` reports
**passed / failed / unanswered**, and its `--strict` exits non-zero on an unanswered row as well as a
failed one. `aify-comms doctor` counts passed, failed and skipped separately and marks a skipped row
`ok: false, skipped: true`, so a consumer keying on `.ok` cannot read it as a pass; its `--strict`
exits on failures only, because on Windows `bridge-running` and `agent-identity` always skip.

## The TUI, and the limit on what it may claim

aify-env is visible, not just a daemon. It can honestly show:

- **Registered services** from `services.json` — reachable or silent, plus whatever each self-reports.
- **Processes it owns** — pid, wrapper, cwd, uptime. Ground truth, because it started them.
- **Traffic through itself** — spawn requests, output bytes. Its own I/O, which it genuinely observes,
  which is what an activity animation may be driven by.

The limit: aify-env knows **processes**, not agents. Alive is not the same as working. A managed-agent
list may show what aify-env owns, annotated with what aify-comms reports when asked, and must not
derive status of its own — deriving it in two places is how two answers start disagreeing.

## Open question

**Shared-host trust**, per the allowlist note above: whether the marker alone is enough, or whether
the installed set should be recorded at install time.

Answered since this was written: the tier kept the name `aify-env`; the request contract is aify-env's
`aify-comms` plugin calling this service's HTTP API (it claims spawn requests and terminal controls,
and runs the launcher file with structured `argv`), so aify-env's host core did not take on the turn-detection and steering code; and
aify-wrapper's `install.sh --all` installs a launcher for every harness found on PATH.
