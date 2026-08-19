# Three-repo separation: program roadmap

**Goal.** Split one product into four components that each own one concern, so a second and third
service can exist without duplicating PTY ownership or depending on aify-comms.

**Status.** Roadmap only. Phase 1 has a full plan beside this file. Phases 2 and 3 are sized, gated
and sequenced here, and get their own plans when the phase before them lands — writing them now would
be fabricating detail nobody has earned yet.

**Prior art in this repo, read before starting any phase:**
[MULTI_SERVICE_STACK_TRACE.md](../../MULTI_SERVICE_STACK_TRACE.md) (what already works and what
blocks a second service) and [AIFY_ENV_BOUNDARY.md](../../AIFY_ENV_BOUNDARY.md) (what moves, what
stays, the derived allowlist, and why doctor asks rather than inspects).

---

## The four components

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
Phase 1  aify-wrapper        independent, blocks nothing, defines the registry
   │                         └─ the registry must land here: the wrapper is its FIRST reader
   ▼
Phase 2  aify-env            needs the registry; new repo; no live-fleet risk until phase 3
   │
   ▼
Phase 3  aify-comms          highest risk in the program — it is the live fleet
```

Phase 1 defines `services.json` because aify-wrapper is its first reader. Deferring the schema to
phase 2 would mean shipping aify-wrapper twice.

Phase 3 is last and must be reversible. It is the only phase that touches a running fleet, and this
project's recorded incidents are almost all in exactly that surface: a spawner colliding with itself,
a bridge reporting OFFLINE and still claiming work, a restart that produced no worker for three
independent reasons.

## Phase gates

A phase is done when its gate is green by measurement, not by assertion.

**Phase 1 — aify-wrapper.** Installing it on a host with N harnesses present produces N launchers and
touches nothing else. A wrapper built against a stale registry reports itself stale rather than
silently launching against one service. `test_wrapper_templates_are_published_in_sync.py` is DELETED,
because aify-comms consumes the package instead of copying it and the duplication it guarded is gone.

**Phase 2 — aify-env.** A process started through aify-env runs under a PTY, streams to a consumer,
and is reaped when it dies. A file without `HARNESS_WRAPPER_VERSION` is refused. `aify-env doctor`
reports `passed / failed / unanswered` and a silent registered service reads `unanswered`, never `ok`.
The TUI shows registered services, owned processes and its own I/O, and claims no agent status.

**Phase 3 — aify-comms.** aify-comms spawns nothing itself; every spawn goes through aify-env. The
`aify-comms` command does not exist. `/health` self-reports build sha, branch and built-at, all from
the stamp, and the repo ships the tooling that compares that report against a checkout. A live
two-session round-trip passes: two agents registered, `comms_send` between them, the target wakes or
queues per capability, and the response threads back.

## Risk, and the one that is not like the others

| risk | phase | handling |
|---|---|---|
| The 16.9k lines of harness semantics follow `terminal-runtime.js` into aify-env | 2 | The phase gate names what may move. Turn detection, stop gating and steering stay. |
| A registry read at launch adds latency to every start | 1 | Read at INSTALL, not at launch. Hermes' MCP discovery window is 0.75s and the repo already lost that fight once. |
| A hand-written file carrying the marker enrols itself | 2 | Acceptable locally. Record the installed set at install time before aify-env is reachable off-host. |
| **aify-comms losing PTY ownership while a fleet is running** | **3** | **The one that can take the fleet down.** Ship behind a flag defaulting to the current behaviour, flip on an idle fleet, keep the old path until a round-trip passes. |

## What this program deliberately does not do

- **It does not move agent status.** `derive()`, the six states and dispatch turns stay in aify-comms.
  Deriving status in two places is how two answers start disagreeing.
- **It does not design aify-dashboard.** It only stops aify-comms from being the thing a dashboard has
  to depend on for liveness.
- **It does not introduce a stream protocol.** Long-poll at a 25s server-side cap already gives push
  latency without a socket to keep alive. Measure before replacing it: the ceiling on hundreds of
  agents is single-worker uvicorn and SQLite write serialisation, not the request rate.
