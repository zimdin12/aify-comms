# Three-repo separation: v0.6 Phases 6-8

**Goal.** Split one product into four components that each own one concern, so a second and third
service can exist without duplicating PTY ownership or depending on aify-comms.

**Version.** This is the REST OF v0.6, not a new release. Phases 0-5 changed only internals: the
wrapper contract, a bridge census, dashboard tests, a bughunt and an e2e baseline. Useful prework,
but nothing an operator receives. The separation is the thing v0.6 was for, so it carries the same
number and the tag waits for it.

**Status.** Roadmap only. Phase 6 has a full plan beside this file. Phases 7 and 8 are sized, gated
and sequenced here, and get their own plans when the phase before them lands — writing them now would
be fabricating detail nobody has earned yet.

**Does it all connect?** [CONNECTION_TRACE.md](../../CONNECTION_TRACE.md) — every link between the
three repos and what proves it, including the one verified against the running service.

**Prior art in this repo, read before starting any phase:**
[MULTI_SERVICE_STACK_TRACE.md](../../MULTI_SERVICE_STACK_TRACE.md) (what already works and what
blocks a second service) and [AIFY_ENV_BOUNDARY.md](../../AIFY_ENV_BOUNDARY.md) (what moves, what
stays, the derived allowlist, and why doctor asks rather than inspects).

---

## The four components

**Where they live.** aify-wrapper: <https://github.com/zimdin12/aify-wrapper>. aify-env:
<https://github.com/zimdin12/aify-env>. aify-comms is the private repo this file lives in.

| | Owns | Must not know |
|---|---|---|
| **aify-wrapper** | The launchers. One per harness present on the host. | Anything about a specific service. |
| **aify-env** | Processes and PTYs on this host. One per host. | Agent semantics. Alive is not working. |
| **aify-comms** | Messaging, dispatch, channels, agent status. | How to allocate a PTY. |
| **aify-dashboard** | Agent-pushed HTML, liveness pages, tasks, docs, projects. | Not in this program. |

**One config, three readers:** `~/.aify/services.json`. Each service's installer writes its own entry.
aify-wrapper reads it at install; aify-env reads it at start. No second source of truth.

**Each component checks itself.** There is no cross-service doctor. Health is self-reported from the
build stamp, never from configuration, and the comparison against a checkout is a developer action
because a running service has no repo. aify-env's doctor collects and displays; it does not inspect.

## Sequencing, and why this order

```
Phase 6  aify-wrapper        independent, blocks nothing, defines the registry
   │                         └─ the registry must land here: the wrapper is its FIRST reader
   ▼
Phase 7  aify-env            needs the registry; new repo; no live-fleet risk until phase 8
   │
   ▼
Phase 8  aify-comms          highest risk in the program — it is the live fleet
```

Phase 6 defines `services.json` because aify-wrapper is its first reader. Deferring the schema to
phase 7 would mean shipping aify-wrapper twice.

Phase 8 is last and must be reversible. It is the only phase that touches a running fleet, and this
project's recorded incidents are almost all in exactly that surface: a spawner colliding with itself,
a bridge reporting OFFLINE and still claiming work, a restart that produced no worker for three
independent reasons.

## Phase gates

A phase is done when its gate is green by measurement, not by assertion.

**Phase 6 — aify-wrapper. MET, except the last clause.** Installing it on a host with N harnesses present produces N launchers and
touches nothing else.

A wrapper built against a stale registry reports itself stale rather than silently launching against
one service — **and this clause was wrongly marked met once.** The fingerprint was baked into every
launcher and printed by `--check`, and nothing compared it to anything: a value written and displayed
is not a check. `aify-wrapper-check` is the consumer that was missing, and it reads the launchers
rather than running them. `test_wrapper_templates_are_published_in_sync.py` **is deleted, and so is
`wrappers/`**: the operator settled Task 6b on 2026-08-20 — aify-comms consumes the package, no
duplicates. `install.sh` renders from `mcp/stdio/node_modules/aify-wrapper/wrappers`, pinned to a sha,
and the swap was proven byte-identical on all six rendered launchers before the copy was removed. The
twin gate in aify-wrapper went with it: with one source of truth there is nothing left to compare.

**The fingerprint clause is now PROVEN on the real system, not only in fixtures.** Run against this
host's actually-installed launchers, `aify-wrapper-check` reports:

```
  ??    claude-aify  no registry fingerprint - installed before this existed?
  ??    codex-aify   no registry fingerprint - installed before this existed?
  ??    hermes-aify  no registry fingerprint - installed before this existed?

0 current, 3 unreadable - reinstall the affected launchers
```

Three things in that output rather than only the last line. It reports `??` and never "current", so a
launcher it cannot read is not counted as fine — the rule this program keeps re-learning. It read the
files rather than running them, which is what makes it safe to point at a pre-contract wrapper at all.

**And the clause was only half true until 2026-08-20: `install.sh` baked the literal string
`unknown`.** aify-comms' installer is the primary path, so every launcher on every host would have read
`??` forever and none could ever read `current` — the right failure direction, telling nobody anything
about the only path that matters. aify-wrapper's own installer had baked the real value all along, so
one field in one template meant different things depending on which installer wrote it. It now calls
the package's own fingerprint tool through `scripts/registry-fingerprint.sh`, and registration moved
ahead of wrapper rendering, because a launcher bakes the registry as it stands and registering
afterwards made every FIRST install produce a launcher stale by the entry it had just added. Proven
both ways: a one-service registry fingerprints `2bc86e1bcae311fa` where an empty one gives
`bcee5b55e534ae7e`, the render bakes the former, and the checker says `1 current` against that registry
and `0 current, 1 stale` against the other.
And `--strict` exits 1, verified separately, so a script can act on the answer instead of only a human
reading it.

The finding itself is operator-facing: every installed launcher on this host predates the fingerprint,
so they need a reinstall. That agrees with `aify-comms doctor`, which reports the bridge and the service
as older than the checkout — the whole install is behind, by design, pending the deploy decision.

**Phase 7 — aify-env. MET by measurement — run the suite for the count rather than trusting one
written here; it was 171, then 191, and is 232 (231 + 1 skip) later the same day, 2026-08-20. Three
figures for one day is the point: every number in prose rots.** A process started through aify-env runs under a PTY, streams to a consumer,
and is reaped when it dies. A file without `HARNESS_WRAPPER_VERSION` is refused. `aify-env doctor`
reports `passed / failed / unanswered` and a silent registered service reads `unanswered`, never `ok`.
The TUI shows registered services, owned processes and its own I/O, and claims no agent status.

**Phase 8 — aify-comms. UNBLOCKED, and stopped where it was told to stop. See docs/PHASE8_STATUS.md.**
The stream aify-env was missing now exists, so delegation can carry a console as well as a spawn.
**This paragraph said the seam was "deliberately unwired". That is out of date and was left standing
after the work it describes was finished** -- exactly the failure `docs inherit intention, not outcome`
names. `TerminalProcessManager.start()` now delegates: `startDelegated()` mirrors `startPty` through
the same state, `_handleOutput`, `_handleExit` and keepalive, and the four control paths (`input`,
`resize`, `stop`, auth-kill) key on `terminal.term` rather than `kind === "pty"`. It is proven against
a REAL aify-env, not a fixture -- output through `onOutput`, exit code 5 through `onExit`, a keystroke
echoed back -- and it is still OFF, because `isEnabled()` needs both `AIFY_COMMS_DELEGATE_SPAWNS` and
`AIFY_ENV_ENDPOINT` and nothing in this repo sets either. Flipping is the operator's, on an idle
fleet.
Original gate, unchanged, for when it resumes: aify-comms spawns nothing itself; every spawn goes through aify-env. The
`aify-comms` command does not exist. `/health` self-reports build sha, branch and built-at, all from
the stamp, and the repo ships the tooling that compares that report against a checkout. A live
two-session round-trip passes: two agents registered, `comms_send` between them, the target wakes or
queues per capability, and the response threads back.

## What is waiting on the operator

Five decisions, scattered across three documents until now, with what each one holds up. Nothing in this
list is blocked on effort; every one of them is a judgement that is not an agent's to make.

**FOUR OF THE FIVE ARE ANSWERED as of 2026-08-20**, and the work each unblocked is done. What remains is
the deploy window, which is the only one with anything at stake in waiting: the gap between checkout and
fleet grows with every commit.

| | decision | what it holds up | where it is argued |
|---|---|---|---|
| 1 | ~~The published git identity in aify-wrapper.~~ **DECIDED 2026-08-20: leave it.** Both addresses are the operator's own and already public. | — | this table |
| 2 | ~~Task 6b — how aify-comms locates the wrapper package.~~ **DECIDED 2026-08-20: consume the package.** Done — pinned npm dependency, `wrappers/` deleted, both drift gates retired. | — | `2026-08-20-aify-wrapper-completion.md`, Task 6b |
| 3 | ~~Shell string versus structural argv.~~ **DECIDED 2026-08-20: carry `argv`, additively.** Built end to end and proven against a real aify-env; the seam delegates and is still flag-off. | — | `docs/PHASE8_STATUS.md` |
| 4 | **The deploy window.** Nothing from this program is deployed: `aify-comms doctor` reports 4 checks needing attention, all of them "older than the checkout". | Everything reaching the fleet. The service, the bridge, the installed skills and the running wrappers are all behind. | `aify-comms doctor` |
| 5 | ~~The `aify-env` name.~~ **DECIDED 2026-08-20: keep it.** It names the tier rather than the coupling, which was the point. | — | `docs/AIFY_ENV_BOUNDARY.md` |

**Decision 3, now answered, was the one that changed most under examination.** The options table treats "parse the
shell string" as the risky choice because quoting bugs live there. They live there ALREADY: the bridge
regex-parses the command to recover `--resume <handle>` and rewrites it to strip the flag, and that
parsing has shipped a defect — codex's and opencode's forms went unrecognised, so the heal path could
never fire and workers got a blank `CODEX_THREAD_ID`. Passing argv would delete a parse rather than add
one.

**Decision 4 is the only one that is time-sensitive**, and only mildly: the gap between checkout and
fleet grows with every commit, and `bridge-current` cannot verify a wrapper that has not been relaunched.

## Risk, and the one that is not like the others

| risk | phase | handling |
|---|---|---|
| The 16.9k lines of harness semantics follow `terminal-runtime.js` into aify-env | 7 | The phase gate names what may move. Turn detection, stop gating and steering stay. |
| A registry read at launch adds latency to every start | 6 | Read at INSTALL, not at launch. Hermes' MCP discovery window is 0.75s and the repo already lost that fight once. |
| A hand-written file carrying the marker enrols itself | 7 | Acceptable locally. Record the installed set at install time before aify-env is reachable off-host. |
| **aify-comms losing PTY ownership while a fleet is running** | **8** | **The one that can take the fleet down.** Ship behind a flag defaulting to the current behaviour, flip on an idle fleet, keep the old path until a round-trip passes. |

## What this program deliberately does not do

- **It does not move agent status.** `derive()`, the six states and dispatch turns stay in aify-comms.
  Deriving status in two places is how two answers start disagreeing.
- **It does not design aify-dashboard.** It only stops aify-comms from being the thing a dashboard has
  to depend on for liveness.
- **It does not introduce a stream protocol.** Long-poll at a 25s server-side cap already gives push
  latency without a socket to keep alive. Measure before replacing it: the ceiling on hundreds of
  agents is single-worker uvicorn and SQLite write serialisation, not the request rate.
