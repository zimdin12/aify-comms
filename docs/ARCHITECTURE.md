# Architecture

**What this file is.** How aify-comms is built, for someone (human or agent) about to change it.
[DECISIONS.md](../DECISIONS.md) holds the *why* for individual non-obvious choices,
[CLAUDE.md](../CLAUDE.md) the working rules for editing the repo, and
[KNOWN_ISSUES.md](../KNOWN_ISSUES.md) what is known-broken. [ARCHITECTURE_PLAN.md](ARCHITECTURE_PLAN.md)
is a proposal from before the service existed; where it disagrees with this file, this file and the
code win.

**Read [the layer rules](#the-layer-rules-and-the-test-that-enforces-each) before your first
change.** Every rule there names the test that fails when it is broken, so a rule is a gate, not
advice.

---

## The tiers, and what reloads each

The boundaries are where deploys, restarts and failures separate. Editing one tier and reloading
another is the most common way a change appears not to work.

| | Runs where | Reload by | Holds |
|---|---|---|---|
| **Service** | containers `aify-comms-service` (API, port 8800) and `aify-comms-dashboard-next` (dashboard, port 8801) | `bash scripts/stamp.sh && docker compose up -d --build` | FastAPI control plane, SQLite, dispatch, the dashboard, the SSE MCP transport |
| **Bridge** | the host, one per agent: the MCP stdio server each agent's runtime starts from `~/.aify-comms` | `install.sh`, **then relaunch the agent** | MCP tools, runtime adapters, the dispatch claim and delivery loops. No PTYs |
| **Host tier** | the host, one per environment: **[aify-env](https://github.com/zimdin12/aify-env)**, a separate repo | restart `aify-env`, which is the operator's action | processes and PTYs, spawn claiming, terminal controls, console streaming. The only spawner |
| **Wrapper** | the host, a launcher on PATH, from the [aify-wrapper](https://github.com/zimdin12/aify-wrapper) package | `install.sh` alone: it is text written at install time | `claude-aify`, `codex-aify`, `hermes-aify` (and `pi-aify`, deprecated): resolve the runtime CLI, export the identity environment, wire the MCP config, exec the runtime |
| **Runtime** | the host, a child of a wrapper | its own process lifecycle | `claude`, `codex`, `hermes`: the coding agent itself |

**The wrapper and the bridge reload in opposite ways.** A bridge is a running process, so new code on
disk does nothing until the agent restarts; `aify-comms doctor`'s `bridge-current` compares the build
each registered agent's bridge reports, and reads `unknown` when none reports one. A wrapper is
generated text, so restarting it does nothing until `install.sh` is re-run; `aify-wrapper-check`
reports a stale one.

The four wrapper bodies are `*.sh.in` templates in aify-wrapper, consumed here as an npm dependency
pinned to a commit. Each reads six `HARNESS_*` inputs from its host rather than knowing it belongs to
aify-comms; see [the wrapper contract](superpowers/specs/2026-08-19-harness-wrapper-contract.md).
claude and codex wrappers are tested by rendering them and running them against a stub runtime on
PATH (`mcp/stdio/tests/wrapper-harness.mjs`). Hermes is guarded by rendered-text assertions instead:
before it execs anything it reaps by agent id, including whatever holds the port derived from that
id, so a test id colliding with a live agent's port would kill the operator's gateway host.

**A bridge is a peer of the service, not a client.** It long-polls for work, claims it, runs it and
reports back. That is why an agent can be *registered* and unable to receive anything: registration
is a row, liveness is a process.

**Editing the wrong tier is silent.** A container rebuild does nothing for `mcp/stdio/`, and
`install.sh` does nothing for a bridge already running: it copies files into `~/.aify-comms/`, and
every running agent keeps executing the copy it loaded at boot. `aify-comms doctor` exists because
each of these paths fails without an error; see CLAUDE.md, "Verify a change actually took effect".

**`aify-comms` is a verifier and starts nothing.** `doctor`, `--check`, `--version` and `--help`
answer; anything else exits 2 and names aify-env. aify-env is the host tier and the only spawner, so
there is no second process for the command to start.

---

## Inside the service

Requests enter at a router and descend. The direction is one-way:

```text
service/routers/**             HTTP surface: route declarations, request/response shape, auth.
        |                      api_v2.py is not a router; it is the include_router calls.
        v
service/api_core/**            One subject per module, no knowledge of HTTP.
service/reconcilers/**         One responsibility per module, driven by the sweep loop.
service/*.py (top-level)       Service-level modules, e.g. dispatch_claim.py and
        |                      terminal_write_queue.py (each owns its own transaction),
        |                      status_engine.py (the pure status derivation), clock.py, env_status.py.
        v
service/db.py, schema.py       SQLite. A pooled connection per call; writes serialise
                               through BEGIN IMMEDIATE.
```

`service/sse/**` is a **parallel entry point, not a layer**: the `comms_*` MCP tools agents reach
over the SSE transport. It calls the same API over HTTP through `service/sse/api_client.py` rather
than importing the handlers, so a tool's behaviour is tested by patching `_api` *in the tool's own
module*; each resolves it locally, by design.

**Why the flatness.** The control plane was once one 20,545-line router module. Logic inside a module
that size is only reachable through the running app, so it can only fail in production. The v0.5
series moved it out one subject at a time into the modules above. `service/terminal_diagnostics.py`
("which line of a dead terminal's output explains the death") is the shape to copy: **put new
behaviour in a module with one subject and import it.**

### Where state lives, and the constraint that follows

Derived agent status is a **process-global in-memory dict**, `_LIVE_STATE_CACHE`, owned by
`service/reconcilers/status_cache.py`. There is no status table; debug status with
`comms_agent_info` or the dashboard.

**Therefore the service must stay single-worker uvicorn.** One process, one event loop. Adding
`--workers 2` does not degrade status; it makes each worker confidently report a different answer.
Moving the cache to a shared store is the prerequisite, not a follow-up.

---

## How a message becomes work

The path most defects live in, and the one `service/tests/e2e/test_message_to_work.py` drives
against a real service over HTTP:

```text
comms_send                     an agent, or the dashboard
  -> POST /messages/send       stored FIRST: a message exists even if nothing can run it
  -> capability lookup         can the target be woken? steered? only queued?
  -> dispatch run created      the audit record: queued -> claimed -> running -> completed
  -> bridge claims it          POST /dispatch/claim, long-poll; ONE claimant wins
  -> runtime adapter delivers  steer into the current turn, or queue as next-turn work
  -> result mirrored back      threaded to the original message
```

Four things about this path are load-bearing and non-obvious:

- **Storage precedes delivery.** A message that cannot be delivered is still a message. Nothing is
  dropped because a target was busy.
- **Steer and queue are opposite choices, not a fallback pair.** `queueIfBusy=true` turns steering
  off. A busy steer-capable target receives a send *into its current turn*; a busy non-steer target
  gets it as next-turn work.
- **A claim is serialised by `BEGIN IMMEDIATE`, and the loser must know it lost.**
  `service/dispatch_claim.py` holds one write transaction across selecting and marking the run, so
  two bridges cannot both take it. Two claimants both acting on one instruction is how this repo
  once lost a fleet.
- **Cleanup keys on state, never on an event.** Many code paths can end a terminal; a cleanup hooked
  to one of them leaves the rest stranded. Reapers sweep for the *state*.

### Foreign text is untrusted, everywhere

A message body, a subject, a run summary, a terminal line: all of it is text somebody else wrote,
rendered into an agent's context where it can read as an instruction. Two mechanisms, both gated:

- `_quote_untrusted_subject` (`service/api_core/serialization.py`) collapses control characters,
  clips, and neutralises quotes. `mcp/stdio/quote-subject.mjs` is its bridge-side twin, and a
  cross-language agreement test pins them together.
- Multi-line foreign text is **fenced**, and the fence escapes internal fences so it cannot be
  closed early.

**Search for the guard's output shape, not its name.** Four sites once hand-typed `"{subject}"`
instead of calling the quoter. A test that only checks the quoter proves nothing about a site that
never called it, which is why `service/tests/test_untrusted_subject_rendering.py` walks the AST for
f-strings that interpolate a subject unquoted.

---

## The bridge side

`mcp/stdio/` is host-side Node. `mcp/stdio/server.js` is the MCP surface; `mcp/stdio/runtimes.js`,
`mcp/stdio/adapters/` and `mcp/stdio/controllers/` hold the per-runtime delivery. Processes and PTYs
belong to aify-env.

The pattern to follow is `mcp/stdio/doctor-predicates.js`: **pure decision logic extracted out of the
bridge so it can fail a test instead of only failing in production.** `mcp/stdio/doctor.js` was
untestable until its predicates moved out, and the first thing the new test caught was a real bug.

Two hazards specific to this tier:

- **An async shutdown is a window, not an instant.** Loop gates that never read `shutdownStarted`
  once let a bridge report OFFLINE and keep claiming work for the whole await chain. Check the flag
  *inside* the loop (`mcp/stdio/loop-gate.mjs`), not only before it.
- **Module-scope mutable state can have an owner; closure-captured state cannot.** Count the direct
  readers of a mutable name before proposing to move it.

---

## The layer rules, and the test that enforces each

Each row is a rule you can break without any obvious symptom, followed by what will tell you.

| Rule | Enforced by |
|---|---|
| No product source file reaches 1000 lines (its scope is in its docstring) | `service/tests/test_no_new_oversized_source_file.py` |
| `service/api_core/` and `service/reconcilers/` never import a router | `service/tests/test_leaves_do_not_import_the_carrier.py` |
| No module-level import cycle among service modules | `service/tests/test_no_import_cycles.py` |
| No module imports a name nothing reaches | `service/tests/test_no_dead_imports.py` |
| Every reconciler has a production caller | `service/tests/test_no_reconciler_is_dead_code.py` |
| Container runtime must not import host-side bridge code | `service/tests/test_service_runtime_boundary.py` |
| One version, in the root `VERSION` file, and nowhere else | `service/tests/test_version_single_source.py`, `mcp/stdio/tests/version-consistency.test.js` |
| Every registered `comms_*` tool is documented, and every documented one exists | `mcp/stdio/tests/skill-consistency.test.js` |
| The two skill trees are byte-identical | `service/tests/test_skill_mirror_parity.py` |
| The Python and JS subject quoters agree byte-for-byte | `service/tests/test_subject_quoting_agrees_across_transports.py` |

**The allowlist for the 1000-line gate is empty, and empty is the end state.** Adding your file to
`oversized-allowlist.json` to turn a red test green is the move the gate exists to stop; it is a
reviewer decision, not a fix.

**The suites and how to run them are in CLAUDE.md, "Testing a change".** `node --check` only
*parses*: it has passed on a module that referenced an undefined name and threw on its first real
call.

---

## Lessons that cost something to learn

Each is a defect class this codebase has shipped, and each will bite a newcomer in the same place.

**No evidence is not a pass.** A check that could not gather evidence must not report ok. Doctor's
`env-bridge` once reported "2 connected" with zero bridges alive because it counted *registered
rows*; `bridge-current` was green-by-default when no bridge reported a build. Distinguish "some
evidence, partial" from "no evidence" and fail the second.

**A test that cannot fail is not a test.** Prove a fix by mutating the product and requiring the test
to go red. A claim-race test here once passed *without* the fix, because sequential claims never
reach the branch; an assertion containing a literal backspace byte could never match, so a check had
never once run. Both looked correct in review.

**Location pins hide defects.** A test asserting that code *lives* at a path proves a line was
written, not that it behaves. Converting these to behavioural tests has found live defects here.

**Measure the destination of a move.** A relocation once put a 6-line helper in its correct owner
and took that file from 995 to 1006 lines. Every other gate was green, because none measured where
the code landed.

**Docs inherit intention, not outcome.** Prose written beside a change describes the *plan*, and no
suite reads prose except for the names it cites. Counts are absent from this file for that reason:
the run is the authority.
