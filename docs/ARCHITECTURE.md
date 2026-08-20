# Architecture

**What this file is.** How aify-comms is actually built, for someone — human or agent — about to
change it. [docs/ARCHITECTURE_PLAN.md](ARCHITECTURE_PLAN.md) is a *proposal* from before the service
existed and is kept for its reasoning, not as a description; where the two disagree, this file and
the code win. [DECISIONS.md](../DECISIONS.md) holds the *why* for individual non-obvious choices,
[CLAUDE.md](../CLAUDE.md) the working rules for editing the repo, and
[KNOWN_ISSUES.md](../KNOWN_ISSUES.md) what is known-broken.

**Read [the layer rules](#the-layer-rules-and-the-test-that-enforces-each) before your first
change.** Every rule there has a test that fails when it is broken, so the rules are not advice —
they are the difference between a change that lands and a red suite you do not understand. Prose
rots; a gate does not. That is why this document names the gate for each claim instead of asking you
to believe the claim.

---

## The three processes

Nothing here is a monolith, and the boundaries are not stylistic — they are where deploys, restarts
and failures separate. Getting the wrong one is the most common way a change appears not to work.

| | Runs where | Reload by | Holds |
|---|---|---|---|
| **Service** | container `aify-comms-service`, port 8800 | `bash scripts/stamp.sh && docker compose up -d --build` | FastAPI control plane, SQLite, dispatch, dashboard, SSE MCP transport |
| **Bridge** | the **host**, one per agent + one per environment | `install.sh` **then relaunch the wrapper** | MCP stdio server, runtime adapters, terminal/PTY ownership, delivery loops |
| **Wrapper** | the host, a shell script on PATH | `install.sh` alone — it is written at install time, so restarting it changes nothing | `claude-aify`, `codex-aify`, `pi-aify`, `hermes-aify`: resolve the runtime CLI, export the identity environment, wire the MCP config, exec the runtime |
| **Runtime** | the host, a child of a wrapper | its own process lifecycle | `claude`, `codex`, `hermes`, `pi` — the actual coding agent |

**The wrapper is a separate tier from the bridge, and confusing them costs a debugging session.** They
reload differently: a bridge is a running process, so new code on disk means nothing until it restarts
(`bridge-current`); a wrapper is generated TEXT, so a restart means nothing until `install.sh` is
re-run (`wrapper-current`). The two doctor checks say opposite things on purpose — one says RESTART,
the other says REINSTALL.

Since v0.6 all four bodies are `*.sh.in` templates rather than `install.sh` heredocs, and each reads
six `HARNESS_*` inputs a host supplies rather than knowing it belongs to aify-comms. They now live in
the [aify-wrapper](https://github.com/zimdin12/aify-wrapper) package, which this repo depends on at a
pinned commit — the local `wrappers/` copy and the hash gates that kept it honest were retired on
2026-08-20 when the duplication ended. See
[the wrapper contract](superpowers/specs/2026-08-19-harness-wrapper-contract.md).

**Only three of the four can be tested by running them.** claude, codex and pi each have a harness
that renders the wrapper, puts a stub on PATH under the runtime's name and executes it. Hermes does
not: before it execs anything it reaps by agent id, and one of those reaps kills whatever holds the
port derived from that id — so a test id colliding with a live agent's port would kill the
operator's gateway host. Hermes is guarded by rendered-text assertions instead, and that asymmetry
is deliberate rather than unfinished.

**A bridge is not a client of the service; it is a peer.** It long-polls for work, claims it, runs
it, and reports back. This is why an agent can be *registered* and completely unable to receive
anything: registration is a row, liveness is a process.

**Editing the wrong tier is silent.** A container rebuild does nothing for `mcp/stdio/`, and
`install.sh` does nothing for a bridge already running — it copies files into `~/.aify-comms/`, and
every running wrapper keeps executing the copy it loaded at boot. `aify-comms doctor` exists because
every one of these paths fails without an error; see CLAUDE.md, "Verify a change actually took
effect". **Never run a bare `aify-comms`** — it starts the environment bridge, supersedes the live
one, and reaps its managed workers. Use `aify-comms --check`.

---

## Inside the service

Requests enter at a router and descend. The direction is one-way, and that is the whole design:

```text
service/routers/**          HTTP surface. Route declarations, request/response shape, auth.
        |                   api_v2.py is NOT a router — it is 15 include_router calls.
        v
service/control_plane.py    Shared helpers + the two queue classes used across status,
        |                   dispatch, terminals, spawn and console. The "carrier".
        v
service/api_core/**         Leaves: one subject per module, no knowledge of HTTP.
service/reconcilers/**      Leaves: one responsibility per module, driven by the sweep loop.
        v
service/db.py, schema.py    SQLite. Single connection per call, single writer.
```

`service/sse/**` is a **parallel entry point, not a layer**: the `comms_*` MCP tools agents call.
It reaches the same API over HTTP through `service/sse/api_client.py` rather than importing the
handlers, which is why a tool's behaviour can only be tested by patching `_api` *in the tool's own
module* — each resolves it locally, by design.

**Why the flatness.** `service/control_plane.py` was `service/routers/api_v2.py` at **20,545 lines**. The
v0.5.x series took it to under 1,000 by moving behaviour into the leaves above, one subject at a
time. The lesson worth carrying: logic inside a 20k-line module is only reachable through the
running app, so it can only ever fail in production. `service/terminal_diagnostics.py` — "which line
of a dead terminal's output explains the death" — is the shape to copy. **Put new behaviour in a
leaf and import it. Do not grow the carrier.**

### Where state lives, and the constraint that follows

Derived agent status is a **process-global in-memory dict**, `_LIVE_STATE_CACHE`, owned by
`service/reconcilers/status_cache.py`. It is not a table. The `agent_live_state` table is vestigial —
read and written by nothing — so do not debug status from a table dump.

**Therefore the service must stay single-worker uvicorn.** One process, one event loop. Adding
`--workers 2` does not degrade status; it makes each worker confidently report a different answer.
Moving the cache to a shared store is the prerequisite, not a follow-up. This replaced an earlier
SQLite-backed cache that was rewritten on every dashboard poll and produced recurring
`database is locked` 503s.

---

## How a message becomes work

The single most useful path to hold in your head, because most defects live in it:

```text
comms_send                     an agent, or the dashboard
  -> POST /messages/send       stored FIRST — a message exists even if nothing can run it
  -> capability lookup         can the target be woken? steered? only queued?
  -> dispatch run created      the audit record: queued -> claimed -> running -> completed
  -> bridge claims it          long-poll; the CAS decides ONE winner
  -> runtime adapter delivers  steer into the current turn, or queue as next-turn work
  -> result mirrored back      threaded to the original message
```

Four things about this path are load-bearing and non-obvious:

- **Storage precedes delivery.** A message that cannot be delivered is still a message. Nothing is
  dropped because a target was busy.
- **Steer and queue are opposite choices, not a fallback pair.** `queueIfBusy=true` forces steer off.
  A busy steer-capable target receives a send *into its current turn*; a busy non-steer target gets
  it as next-turn work.
- **Claiming is a compare-and-swap and the loser must know it lost.** Two bridges reaching one
  instruction both acting on it is how this repo lost a fleet. See
  `service/tests/test_two_bridges_cannot_claim_one_control.py`.
- **Cleanup keys on state, never on an event.** ~26 different code paths can end a terminal; a
  cleanup hooked to one of them leaves the other 25 stranded. Reapers sweep for the *state*.

### Foreign text is untrusted, everywhere

A message body, a subject, a run summary, a terminal line — all of it is text somebody else wrote,
rendered into an agent's context where it can read as an instruction. Two mechanisms, and both are
gated:

- `_quote_untrusted_subject` (`service/api_core/serialization.py`) collapses control characters,
  clips, and neutralises quotes. `mcp/stdio/quote-subject.mjs` is its bridge-side twin, and a
  cross-language agreement test pins them together.
- Multi-line foreign text is **fenced**, and the fence escapes internal fences so it cannot be
  closed early.

**Search for the guard's output shape, not its name.** Four sites once hand-typed `"{subject}"`
instead of calling the quoter; a quote in the subject freed the text into an agent's context. A test
that only checks the quoter works proves nothing about the site that never called it — which is why
`service/tests/test_untrusted_subject_rendering.py` walks the AST for f-strings that interpolate a
subject unquoted, rather than grepping.

---

## The bridge side

`mcp/stdio/` is host-side Node. `mcp/stdio/server.js` is the MCP surface; `mcp/stdio/runtimes.js` and the per-runtime
adapters own processes and PTYs.

The pattern to follow is the `*-predicates.js` modules: **pure decision logic extracted out of the
bridge so it can fail a test instead of only failing in production.** `mcp/stdio/doctor.js` was untestable
until its predicates moved out, and the first thing the new test caught was a real bug.

Two hazards specific to this tier:

- **An async shutdown is a window, not an instant.** Twelve loop gates once never read
  `shutdownStarted`, so a bridge reported OFFLINE and then kept claiming work for the whole await
  chain. Check the flag *inside* the loop, not only before it.
- **Module-scope mutable state can have an owner; closure-captured state cannot.** Count the direct
  readers of a mutable name before proposing to move it.

---

## The layer rules, and the test that enforces each

Each row is a rule you can break without any obvious symptom, followed by what will tell you.

| Rule | Enforced by |
|---|---|
| No product source file reaches 1000 lines (`.py`, `.js`, `.mjs`, repo-wide) | `service/tests/test_no_new_oversized_source_file.py`, `mcp/stdio/tests/no-new-oversized-source-file.test.js` |
| A leaf must never import `service.control_plane` | `service/tests/test_leaves_do_not_import_the_carrier.py` |
| No module-level import cycle among service modules | `service/tests/test_no_import_cycles.py` |
| No module imports a name nothing reaches | `service/tests/test_no_dead_imports.py`, `service/tests/test_no_orphaned_imports_in_control_plane.py` |
| Container runtime must not import host-side bridge code | `service/tests/test_service_runtime_boundary.py` |
| One version, in the root `VERSION` file, and nowhere else | `service/tests/test_version_single_source.py`, `mcp/stdio/tests/version-consistency.test.js` |
| Every registered `comms_*` tool is documented, and every documented one exists | `mcp/stdio/tests/skill-consistency.test.js` |
| Moving a declaration out of `service/new_dashboard/app.js` changes nothing else | `service/new_dashboard/extraction-proof.test.mjs` |
| The Python and JS subject quoters agree byte-for-byte | `service/tests/test_subject_quoting_agrees_across_transports.py` |

**The allowlist for the 1000-line gate is empty, and empty is the end state.** Adding your file to
`oversized-allowlist.json` to turn a red test green is the exact move the gate exists to stop; it is
a reviewer decision, not a fix.

### Three suites, all green before a commit

```bash
python -m pytest service/tests -q                      # service + architecture gates
cd mcp/stdio && node tests/run-all.mjs                 # bridge
cd service/new_dashboard && node --test *.test.mjs     # dashboard modules
```

`node --check` only *parses*. It has passed on a module that referenced an undefined name and threw
on its first real call, so it is a smoke test, not a test.

---

## Lessons that cost something to learn

Not style preferences. Each of these is a defect class this codebase has actually shipped, and each
will bite a newcomer in the same place.

**No evidence is not a pass.** A check that could not gather evidence must not report ok. Doctor's
`env-bridge` reported "2 connected" with zero bridges alive because it counted *registered rows*;
`bridge-current` was green-by-default when no bridge reported a build at all. Distinguish "some
evidence, partial" from "no evidence" and fail the second. A green check that verified nothing is
worse than a red one.

**A test that cannot fail is not a test.** Prove a fix by mutating the product and requiring the test
to go red. Two examples from this repo: a CAS race test passed *without* the fix, because sequential
claims never reach the branch; and an assertion containing a literal backspace byte could never
match, so a no-async check had never once run. Both looked correct in review.

**Location pins hide defects.** A test asserting that code *lives* at a path proves a line was
written, not that it behaves. Converting these to behavioural tests has found live defects here.

**Measure the destination of a move.** A v0.5.4 relocation put a 6-line helper in its correct owner
and took that file 995 → 1006 lines. Every existing gate was green, because none of them measured
where the code landed.

**Docs inherit intention, not outcome.** Prose written beside a change describes the *plan*, and
nothing in any suite reads prose. Counts in this file are deliberately absent for that reason: the
run is the authority. When you need a number, measure it — do not quote one from a document,
including this one.
