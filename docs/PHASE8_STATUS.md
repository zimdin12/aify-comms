# v0.6 Phase 8 — aify-comms delegating to aify-env: where it stopped, and why

Phase 8 moves spawning out of aify-comms. The instruction was to build it behind a flag defaulting to
today's behaviour and stop before flipping it. It stopped earlier than the flag, for a reason worth
writing down rather than working around.

## Built

`mcp/stdio/env-client.mjs` and its 10 tests. Start, stop, list and health against aify-env's contract.

**Off by default, and keyed on the endpoint rather than a separate boolean** — one thing to get right,
because a flag that is on with nowhere to talk to is a state nobody wants to debug at the moment an
agent will not start. Verified rather than asserted: `AIFY_ENV_ENDPOINT` is unset in this environment,
nothing in the repo sets it, and nothing imports the client yet. Shipping the file changes nothing.

It reports rather than throws. "Unreachable" and "refused" are different answers and a caller falls
back differently on each; an exception at that boundary makes them identical to a catch block, which
then does the wrong one.

## Not built, and this is the finding

**`TerminalProcessManager.start()` is the single seam** — one method, one singleton
(`TERMINAL_MANAGER`), so the wiring itself is small. It was not written, because writing it would have
produced a path that cannot be turned on:

> **aify-env has no output stream.** Start, stop and list are request/response. A managed agent's
> console needs its output *continuously* — that is what `onOutput`, the batching in
> `TerminalProcessManager`, and the dashboard console all consume.

Delegation can therefore carry a spawn but not a console. Wiring the seam now would mean a flag whose
"on" position loses every managed agent's console, which is not a flag — it is a trap with a default.

The operator's hard requirement is explicit on this: managed agents must show real TUI in the web
console. That is not tradeable, so the protocol grows a stream before the seam moves.

## What Phase 8 needs, in order

| | | size |
|---|---|---|
| 1 | A stream endpoint on aify-env — `GET /processes/:id/output`, server-sent events or a socket | medium |
| 2 | An `EnvClient` subscriber feeding the same `onOutput` the local path feeds | small |
| 3 | Wire `TerminalProcessManager.start()` behind the flag, default off | small |
| 4 | Prove default-off is byte-identical: same spawn, same manager, same output | medium — this is the real work |
| 5 | Flip on an IDLE fleet, keep the local path until a live two-session round-trip passes | operator |

Item 4 is where the effort is. Item 5 is the operator's, on a fleet with nothing running.

## What has NOT been touched

No spawn path changed. No process was started, stopped or routed through aify-env. The bridge, the
manager and the dashboard console behave exactly as they did.
