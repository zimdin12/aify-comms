# v0.6 Phase 8 — aify-comms delegating to aify-env: what is built, and what is deliberately not

Phase 8 moves spawning out of aify-comms. The instruction was to build it behind a flag defaulting to
today's behaviour and stop before flipping it.

## Built

**`mcp/stdio/env-client.mjs`** and its 10 tests — start, stop, list and health against aify-env.

**Off by default, and keyed on the endpoint rather than a separate boolean.** One thing to get right:
a flag that is on with nowhere to talk to is a state nobody wants to debug at the moment an agent will
not start. Verified rather than asserted — `AIFY_ENV_ENDPOINT` is unset in this environment, nothing
in the repo sets it, and nothing imports the client. Shipping the file changes no behaviour.

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

**`TerminalProcessManager.start()`** now consults an injected `envDelegation` before dispatching. With
`AIFY_ENV_ENDPOINT` unset — which nothing in this repo sets — it is one boolean and the next two lines
are exactly what happened before. Seven tests pin that, including one that reads the REAL environment
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
`command` is a shell command STRING that it composes.

aify-env takes a `launcher` path and allowlists it by reading `HARNESS_WRAPPER_VERSION` out of the
file. That is the whole safety model: a host service that runs programs for any caller is remote code
execution unless something constrains what it runs.

Those two do not meet, and the three ways to make them meet are not equivalent:

| | what it costs |
|---|---|
| **Parse the shell string into launcher + args** | Quoting bugs live exactly here. This project already shipped one from a hand-typed quote in a guard, and one from an unescaped backtick in a heredoc. A parser that is wrong on a path with a space produces an agent that will not start, or worse, starts something else. |
| **Let aify-env accept shell strings** | Destroys the allowlist. `bash -lc "<anything>"` passes any marker check, because the thing being executed is bash. |
| **Have aify-comms pass launcher + args structurally** | Correct, and it reaches upstream into how every managed command is composed. |

The third is the right answer and it is not a small edit, so it is where this stops. Choosing the
first two would trade a real safety property for convenience, and doing that quietly is exactly what
the flag was put in to prevent.

| | | size |
|---|---|---|
| 1 | ~~A stream endpoint on aify-env~~ | **done** |
| 2 | ~~An `EnvClient` subscriber~~ — `subscribeOutput`, 5 tests | **done** |
| 3 | ~~Wire `TerminalProcessManager.start()` behind the flag, default off~~ | **done, and it refuses** |
| 3b | A `term` shim over `EnvClient` — the endpoints it needs (input, resize) now exist | blocked on the shell-string decision above |
| 4 | **Prove default-off is byte-identical**: same spawn, same manager, same output, same healing | medium — this is the work |
| 5 | Flip on an IDLE fleet; keep the local path until a live two-session round-trip passes | operator |

Item 4 is where the effort sits, and it is not a formality. `TerminalProcessManager` does more than
spawn: output batching, auto-answer, console keepalive, and a heal path that restarts a session
without `--resume`. A delegated process has to arrive at all of that identically or the difference
shows up as an agent that behaves subtly differently and nobody can say why.

## What has NOT been touched

No spawn path changed. No process was started, stopped or routed through aify-env by aify-comms. The
bridge, the manager and the dashboard console behave exactly as before.
