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

## Not built, on purpose

**`TerminalProcessManager.start()` is the seam** — one method, one instance (`TERMINAL_MANAGER`). It is
unwired, because wiring it is the step that can take a fleet down and the remaining work is the
*proof*, not the plumbing.

| | | size |
|---|---|---|
| 1 | ~~A stream endpoint on aify-env~~ | **done** |
| 2 | An `EnvClient` subscriber feeding the same `onOutput` the local path feeds | small |
| 3 | Wire `TerminalProcessManager.start()` behind the flag, default off | small |
| 4 | **Prove default-off is byte-identical**: same spawn, same manager, same output, same healing | medium — this is the work |
| 5 | Flip on an IDLE fleet; keep the local path until a live two-session round-trip passes | operator |

Item 4 is where the effort sits, and it is not a formality. `TerminalProcessManager` does more than
spawn: output batching, auto-answer, console keepalive, and a heal path that restarts a session
without `--resume`. A delegated process has to arrive at all of that identically or the difference
shows up as an agent that behaves subtly differently and nobody can say why.

## What has NOT been touched

No spawn path changed. No process was started, stopped or routed through aify-env by aify-comms. The
bridge, the manager and the dashboard console behave exactly as before.
