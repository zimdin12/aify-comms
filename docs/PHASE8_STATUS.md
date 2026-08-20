# v0.6 Phase 8 — aify-comms delegating to aify-env: what is built, and what is deliberately not

Phase 8 moves spawning out of aify-comms. The instruction was to build it behind a flag defaulting to
today's behaviour and stop before flipping it.

## Built

**`mcp/stdio/env-client.mjs`** and its tests — start, stop, list, health and output against aify-env.

**Off by default, and it takes TWO things to turn on:** `AIFY_COMMS_DELEGATE_SPAWNS=1` **and**
`AIFY_ENV_ENDPOINT`.

The first version keyed on the endpoint alone, reasoning that one thing is easier to get right than
two. That was wrong, and the reason is concrete: `AIFY_ENV_ENDPOINT` is the variable aify-env's **own**
doctor and TUI read to find the daemon. An operator who exported it to look at their environment would
have made every managed spawn in aify-comms refuse. Knowing where aify-env is says nothing about
wanting to send work there — two questions, two answers.

Only `1`, `true`, `yes` or `on` count. A truthiness check would read `"0"` and `"false"` as on, which
is the worst possible direction for this particular switch.

It reports rather than throws. "Unreachable" and "refused" are different answers and a caller falls
back differently on each; an exception at that boundary makes them identical to a catch block, which
then does the wrong one.

**The blocker that stopped this phase is now GONE.** aify-env had no output stream, so delegation could
have carried a spawn and lost every managed console — a flag whose "on" position breaks the operator's
hard TUI requirement is a trap, not a flag. `GET /processes/:id/output` now exists: server-sent events,
a bounded most-recent replay for consumers that attach late, and a 404 that is distinct from an open
stream that is merely quiet. Proven end to end over a socket, attaching 300 ms after the process
started and receiving both what was printed before and what came after.

## The seam is wired, and it REFUSES

**`TerminalProcessManager.start()`** consults an injected `envDelegation` before dispatching, and
`TERMINAL_MANAGER` is **wired to a real one** — which for a while it was not. The constructor defaults
`envDelegation` to null, the production call site omitted it, and the flag was therefore a **placebo**:
setting the variable did nothing at all. Every seam test injects the dependency, so none of them could
see it — a unit test of a seam cannot see a gap at the call site, because the test *is* the call site.
Three tests now assert the production wiring, and removing it reddens all three.

With delegation off it is one boolean and the next two lines are exactly what happened before. Seven tests pin that, including one that reads the REAL environment
rather than a fixture, and mutation-checking the branch reddens three of them.

When the flag IS set it **throws, naming what is not delegated**, rather than half-working. That is the
important choice. A delegated process fed through `_handleOutput` and `_handleExit` would inherit the
output batching, the auto-answer and the classification for free — which is why parity is reachable at
all, and also why a half-delegated path would LOOK right. Meanwhile `state.term` is used to write,
resize and kill, and the console keepalive probes it. Without a shim for those, flipping the flag would
produce agents that are subtly different in ways nobody could attribute. Refusing puts that at the
point of use instead of leaving it in a document.

The seam tests spawn NOTHING. The seam is a dispatch decision, so dispatch is what is asserted, with
the local methods overridden to record that they were reached. Actually spawning drags in a pty, a
keepalive and a teardown that raises "Signals not supported on windows" — three ways for the file to
fail for reasons unrelated to the branch.

## The finding that ends this phase: aify-comms passes a SHELL STRING, aify-env takes a FILE

`TerminalProcessManager` runs `cmd /d /s /c <command>` on Windows and `bash -lc <command>` elsewhere.

**Traced, not assumed:** there is one production caller, `terminal-control-loop.mjs:87`, and the string
comes from `terminal.command || control.body` — it arrives **from the service**. The bridge does not
compose it. So passing launcher and args structurally is a change to the service-to-bridge contract,
not a bridge-local edit, and every terminal row already queued carries a command string.

aify-env takes a `launcher` path and allowlists it by reading `HARNESS_WRAPPER_VERSION` out of the
file. That is the whole safety model: a host service that runs programs for any caller is remote code
execution unless something constrains what it runs.

Those two do not meet, and the three ways to make them meet are not equivalent:

| | what it costs |
|---|---|
| **Parse the shell string into launcher + args** | Quoting bugs live exactly here. This project already shipped one from a hand-typed quote in a guard, and one from an unescaped backtick in a heredoc. A parser that is wrong on a path with a space produces an agent that will not start, or worse, starts something else. |
| **Let aify-env accept shell strings** | Destroys the allowlist. `bash -lc "<anything>"` passes any marker check, because the thing being executed is bash. |
| **Have aify-comms pass launcher + args structurally** | Correct. Costed here as reaching "upstream into how every managed command is composed" — **measured since, and smaller than that reads.** See below. |

### Measured: the structural form already existed, one line from the surface

Every managed command comes from one place — `adapter.console_command(...)`, via
`_default_console_command` in `service/api_core/capabilities.py`. There are five adapters, and every
one of them was already building a list and throwing the structure away on its last line:

```python
parts = ["claude-aify", "--aify-agent", agent_id]
if handle:
    parts.extend(["--resume", handle])
return " ".join(parts)          # <- the argv existed until here
```

So `console_argv` is now the value each adapter returns and `console_command` is a view of it, derived
once in the base class. **No behaviour changed**: the strings are byte-identical, pinned by literals in
`test_console_command_resume.py`, and dropping `--auto` reddens it.

Two further measurements bear on the decision:

- **Four of the five runtimes never had a command string to parse.** A managed pi, hermes, codex or
  opencode worker gets a SENTINEL — `aify://virtual-rpc/<runtime>` — and nothing executes it. The shell
  string only ever mattered for the runtimes that take a real PTY.
- **No argv element contains a space**, asserted for every runtime and every case. While that holds the
  string and the list carry the same information, so switching between them loses nothing. The test
  fails the day it stops holding, which is the day the structural form stops being optional.

**This does not decide anything and does not delegate anything.** No spawn path changed, the bridge
receives exactly what it received before, and the seam still refuses when the flag is on. It removes one
argument from the open question — that the structural form would have to be built from scratch — and
leaves the question where it belongs.

### What option three still needs, end to end

Traced against both sides rather than estimated. `POST /processes` on aify-env takes a `launcher` that
it reads itself, fails closed if unreadable, judges, and derives the interpreter from — so the file it
judges is always the file it runs. It requires a **readable path**, and it deliberately does **not**
search PATH for the launcher: that would hand the choice of what executes to whatever PATH says. There
is now a test in aify-env pinning that, with the absolute-path control that makes its refusal meaningful.

An adapter's `argv[0]` is a bare **name** — `claude-aify`. So the chain is:

| | | where it lives |
|---|---|---|
| 1 | the service can produce argv | **done** - `console_argv`, five adapters |
| 2 | argv reaches the bridge at all | **unbuilt, and it IS a service-to-bridge contract change** |
| 3 | resolve `argv[0]` to an absolute path | unbuilt; bridge-local *once 2 exists*, since only the host knows its own PATH |
| 4 | call aify-env with `{launcher: <path>, args: argv[1:]}` | unbuilt, small once 3 exists |
| 5 | a `term` shim: write, resize, kill, console keepalive | 3b, the real remaining work |

**CORRECTION.** An earlier revision of this section said steps 2 and 3 "do not cross the service
boundary at all". That was wrong, and it understated the cost. Traced since: the command reaches the
bridge as ONE STRING - `terminal_sessions.command`, copied into the control payload as `"command"`
and read as `terminal.command || control.body`. The argv never leaves the service. `console_argv` made
the value exist; it is still joined before anything stores it, so PRODUCING argv is done and
TRANSPORTING it is not. Step 3 is bridge-local only if the bridge has an argv, and today it has a
string.

What survives of the narrowing is smaller but real: the transport can be **additive**. The carrier is
the TERMINAL OBJECT, not the control payload - `terminal-control-loop.mjs` reads `terminal.command` as
a property and `control.body` as a plain string, and never parses the payload JSON at all. So
`terminal.argv` sits beside `terminal.command`: an old bridge reads only `.command` and is undisturbed,
a new one prefers `.argv`, and every terminal row already queued keeps working because `command` stays.
A contract change, but a backward-compatible one rather than a rewrite of how every managed command is
composed.

(That carrier detail is itself a correction. The first version of this paragraph named the control
payload, on the assumption that a JSON field would be ignored by an old reader. True of JSON, but the
bridge does not read that JSON - so the claim was right for the wrong reason, which is the kind that
survives review and fails in practice.)

### The bridge ALREADY parses that shell string, and it has already cost a defect

The table above weighs option 1 as introducing quoting bugs: "quoting bugs live exactly here". True, and
incomplete — they live there **today**, in the design we already run.

`terminal-control-loop.mjs` takes the command string and calls `extractTerminalSessionHandle`, which is
`extractRuntimeSessionHandleFromCommand`: a set of per-runtime regexes plus `unquoteShellToken`, run
over the command to recover `--resume <handle>`. A sibling, `runtimeCommandWithoutResume`, REWRITES the
string to strip the same flag, which is how the heal path starts a fresh session.

That parsing has already failed in production. The regex set did not recognise codex's or opencode's
resume forms, so `runtimeCommandWithoutResume` returned both unchanged — the heal could never fire for
either, because it only proceeds when the stripped command DIFFERS — and the handle came back empty, so
workers were handed a blank `CODEX_THREAD_ID`. The comment above that flag table records it.

So the honest comparison is not "a safe string versus a risky parse". Both options parse; one of them
parses **already**, and argv would DELETE that parse rather than add one — finding a flag in a list and
taking the next element needs no regex and no unquoting.

**Checked and rejected, so nobody repeats it:** I expected to find that the terminal row already carried
`session_handle` and the bridge was re-deriving data it had been given. It does not. `terminal_sessions`
carries `command` and nothing else of the kind — the four `session_handle` columns in the schema belong
to `agents`, `bridge_instances`, `spawn_requests` and `agent_sessions`. The bridge derives the handle
because it genuinely has no other source, which makes this a missing field rather than a redundant one,
and either argv or an explicit handle column would answer it.

Step 5 is unchanged and is still the work. None of this is built, and nothing here delegates.

The third option is the right answer, and where this stops. Choosing the
first two would trade a real safety property for convenience, and doing that quietly is exactly what
the flag was put in to prevent.

| | | size |
|---|---|---|
| 1 | ~~A stream endpoint on aify-env~~ | **done** |
| 2 | ~~An `EnvClient` subscriber~~ — `subscribeOutput` over SSE | **done** |
| 3 | ~~Wire `TerminalProcessManager.start()` behind the flag, default off~~ | **done, and it refuses** |
| 3b | A `term` shim over `EnvClient` — the endpoints it needs (input, resize) now exist | blocked on the shell-string decision above |
| 4 | ~~Prove default-off is byte-identical~~ — by reconstruction, on both files | **done** |
| 5 | Flip on an IDLE fleet; keep the local path until a live two-session round-trip passes | operator |

## Item 4, done: what "default-off is byte-identical" now rests on

`TerminalProcessManager` does more than spawn — output batching, auto-answer, console keepalive, and a
heal path that restarts a session without `--resume`. Re-testing each of those would have been the
obvious way to show they still work, and it would have been the wrong one: they were never changed, so
those tests would pass on day one and keep passing whatever the seam did next.

The claim is proven structurally instead, in two halves that compose:

1. **The files are pre-seam plus declared blocks.** `seam-is-additive-only.test.js` reconstructs each
   pre-seam file by removing exactly the blocks the seam declares it added, and requires equality with
   a tracked fixture taken from the commit before it. Any edit anywhere else fails it.
2. **Those blocks are inert when the flag is off** — the seven branch tests, plus three that assert the
   production call site actually supplies the dependency.

Together: batching, auto-answer, keepalive and healing are untouched **by construction**. That is a
stronger statement than four behavioural tests, and far cheaper to keep true.

**BOTH files, because the production path is two.** `terminal-runtime.js` holds the guard;
`terminal-manager.mjs` is the call site that supplies it. Proving only the guard would have proven the
half that was never broken — the placebo bug was at the call site.

Mutation-checked in both directions: a single added space on an unrelated line in either file reddens
its reconstruction, and reverting restores green.

**It normalises line endings before comparing, deliberately.** `terminal-manager.mjs` is CRLF in this
working tree and `terminal-runtime.js` is LF, and git rewrites both per the checkout's `core.autocrlf`.
Pinning those bytes would test the checkout rather than the code, and would go red on a colleague's
machine for a reason having nothing to do with the seam. The first version did compare raw bytes and
reported the CRLF file as differing in all 185 lines.

**Retire this test when the flag is flipped.** It pins a phase, not an invariant: once delegation
actually delegates, "identical to pre-seam" stops being the property anyone wants.

## What has NOT been touched

No spawn path changed. No process was started, stopped or routed through aify-env by aify-comms. The
bridge, the manager and the dashboard console behave exactly as before.
