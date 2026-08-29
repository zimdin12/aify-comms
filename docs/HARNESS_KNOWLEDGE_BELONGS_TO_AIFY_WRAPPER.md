# Harness-driver semantics belong to aify-wrapper

**Status: PROPOSED.** The ownership direction is agreed. The interface, the extraction boundary and
the migration are not designed yet, and this document deliberately stops short of claiming they are.

Design capture, 2026-08-29. The operator's correction, my measurement, the reviewer's REVISE, and what
each of us had wrong.

Companion to [AIFY_ENV_BOUNDARY.md](AIFY_ENV_BOUNDARY.md), whose ownership table already says this and
whose file list never followed it. Nothing here is built.

---

## The correction that started it

I argued repeatedly on 2026-08-29 that the bridge's ~31k lines of agent semantics must stay in
aify-comms, because aify-env must not learn what an agent is. The premise is right. The conclusion
does not follow, and the operator said so:

> hermes is harness. aify-wrapper knows about harnesses, aify-env knows about how to run and managed
> processes like aify-wrappers. aify-comms is just a service that uses these 2 and has communication.
> i plan to make other services like aify-comms.

"Not aify-env's" is not the same as "aify-comms'". There is a third component, and
AIFY_ENV_BOUNDARY.md's own table already gives it harnesses.

## What the measurement does and does not show

`mcp/stdio`, 31,823 lines across 175 files, bucketed **by filename prefix** on 2026-08-29:

| lines | files | bucket |
|---|---|---|
| 15,013 | 56 | harness-named — `pi-session.js` (993), `codex-session.js` (803), `hermes-managed-host.js` (728), `hermes-delivery-loop.mjs` (696) |
| 4,942 | 33 | process and environment plumbing |
| 3,124 | 22 | service-named |
| 8,744 | 64 | the rest — doctor (1,502), `server.js` wiring, usage collection |

aify-env for contrast: 3,686 lines, 19 files, and the token "agent" appears 12 times, every one in a
comment.

**This shows duplication PRESSURE. It does not show an extractable boundary, and it is not the
argument.** The reviewer's objection is correct and worth stating at full strength: the files named as
the strongest evidence are mixed compositions. `hermes-delivery-loop.mjs` imports aify HTTP, liveness
heartbeat, run reporting, gateway lifecycle, the turn detector and the claim cycle.
`hermes-delivery-run.mjs` builds Hermes frames *and* performs dispatch claim, requeue, delivered and
failed transitions. Moving those FILES would move aify-comms semantics into aify-wrapper.

**The unit of ownership is a capability, not a filename and not a line count.** The buckets above are
by name; they must be redone at symbol and import-edge level, in four classes — PURE_DRIVER,
SERVICE_ADAPTER, PROCESS_OWNER, COMPOSITION — failing on anything unclassified, before any percentage
is quoted again. Shared module state and side effects count, not just names.

**And "service #2 duplicates the half" is an assumption, not a measurement.** It assumes a second
service needs every persistent-session and turn-detection mode. What it plausibly needs is a common
driver contract. Recording service #2's actual requirements is prerequisite work, not a detail.

## Library, not daemon — under a constraint

aify-wrapper should own harness-driver semantics as a **library**. No case for a daemon exists today:
a daemon adds protocol, auth, IPC latency, lifecycle, compatibility and a third deployed identity
before the interface exists.

**"An import costs nothing at runtime" is false, and it was my claim.** Imported code can open
WebSockets, poll, own session registries, spawn gateway hosts and hold mutable pools. Two services
importing today's stateful loops gives two owners racing one harness — precisely the collision the
split exists to remove. So library is acceptable only under this constraint:

- aify-wrapper owns harness PROTOCOL and DRIVER logic;
- aify-env owns every physical process, PTY, kill, wait and resize handle;
- each service owns its adapter and schedules its own cadence;
- **the driver library owns no global session pool, no daemon loop, no heartbeat, no dispatch claim,
  no service HTTP, no retry cadence and no process supervision.**

If Hermes protocol state cannot be expressed without one host-global mutable owner, that is the real
case for a broker process, and it should be an explicitly governed harness broker supervised by
aify-env. Not hidden inside aify-env, and not justified as a cheap import. Do not rule a daemon out
until a spike proves the driver can be instance-scoped over injected handles.

**aify-wrapper is not that library today.** Its tree is launch templates, registry parsing, harness
detection, install and staleness checks. It has no turn, session or steer driver. aify-comms consumes
its templates and registry utilities as a pinned dependency, and nothing more.

"Thin" should mean **no daemon and narrow exports** — separate subpaths such as `drivers/hermes`, so
launcher consumers do not load runtime drivers and the driver ABI can version independently. It should
not mean pretending 15k lines is thin.

## Where Hermes splits

Neither `hermes-delivery-loop.mjs` nor `hermes-managed-host.js` belongs wholesale to either side.

**Toward aify-wrapper:** gateway frame construction and parsing; session active-list interpretation
and resumability rules; `prompt.submit` versus `session.steer`/interrupt semantics; the turn-detector
state machine; harness-specific terminal and error classification; a driver capability descriptor.

**Stays in aify-comms' host adapter:** claim, requeue, delivered and failed transitions; dispatch IDs,
agent IDs, reply and contract semantics; service HTTP and auth; heartbeat and liveness reporting;
mapping driver events into status and run records; retry cadence that is about dispatch rather than
about Hermes.

**Stays in aify-env:** spawn, attach, stdin, stdout, resize, signal, wait; process identity, custody
and restart. No "turn ended", no "agent working", no dispatch or session-resume interpretation.

`hermes-delivery-loop` remains the service-owned composition root and shrinks as pure driver pieces
leave. **Do not move the loop and then punch callback holes back to the service** — that is aify-comms
semantics living in aify-wrapper under dependency-injection camouflage.

Proposed minimum interface:

- **in:** harness descriptor, opaque session key, command (`submit` | `steer` | `interrupt` |
  `resume`), injected process/transport handle
- **out:** typed events — `accepted`, `output`, `turn_busy`, `turn_idle`, `resumable`,
  `terminal{cause}`
- **never present:** agentId, dispatchRunId, reply obligation, service URL or API key, heartbeat,
  claim or requeue

The caller owns timers and retry cadence. The driver owns deterministic state transition and parsing.

## The `rest` bucket is mixed and changes the ratio

It cannot stay "rest" in an ownership argument:

- **doctor** (1,502) holds service build checks, bridge install and runtime-process checks,
  harness-specific Codex auth and quota logic, Hermes orphan-loop inspection, skill-install checks.
  Classify by CHECK, not by file.
- **`usage-collector.js`** (544) is strongly harness and provider specific: Claude transcript layout,
  Claude OAuth, Codex rollout files, Hermes and Codex auth-store semantics. Raw collection and parsing
  belong with the drivers; aggregation and posting are service behaviour.
- **`server.js`** is a composition root and mixed by design. Its line count says nothing about target
  ownership.
- **controllers** encode both service routing and harness capability. Policy selection is the service's;
  driver implementation is aify-wrapper's.

## Sequence

1. **Fix the staleness.** Small, independent, and it is what is actually costing the operator.
2. **Define and spike the driver contract**, on one narrow vertical slice — a pure turn detector or
   frame builder — consumed by aify-comms plus a fake service-2 contract test. Do not move the 15k.
   **This comes before service #2 exists**, because a hurried second consumer will define a bad
   interface. Waiting until the duplication is real is waiting too long; that was my error.
3. **Have aify-env supervise the existing service adapter.** Remove the user-facing PATH alias only
   after registry launch, restart, rollback and ownership convergence are proven. "Delete the
   `aify-comms` command" means delete the **public bare alias**, not the executable entrypoint — the
   process still has to start, supervised, and the 2026-08-11 incident was about the bare invocation.
4. **Migrate harness by harness**, after the spike proves ownership.

## The staleness, which is the near-term work

`install.sh` writes the bridge files. Nothing restarts the bridge. The process keeps what it loaded at
boot, indefinitely, and the only thing that notices is `aify-comms doctor`'s `bridge-current`.

Measured on the operator's host, 2026-08-29: bridge running `579dd546` from 25 August 04:53 against
installed files at repo HEAD. Four days. Two user-visible defects sat fixed on disk the whole time —
the empty AGENT column, and transcript saving disabled for every managed agent.

**The design must not be "write files; the process notices; it exits".** Install can expose a mixed
tree mid-copy, and self-exit can restart into partial bytes or into a restart loop. Instead:

- immutable, content-addressed release directories;
- write and verify fully, then **atomically switch** the registry's desired artifact;
- the adapter reports its **loaded** fingerprint;
- aify-env compares loaded against desired and restarts generically, learning nothing about harnesses.

**Three identities have to be covered**, not one: the desired adapter artifact, the loaded adapter
artifact, and the loaded driver package and ABI. A pin plus a lockfile is *source selection*, not
runtime proof. The adapter self-reports the exact driver version and content fingerprint; the service
doctor compares that to the consumed lock; aify-env only compares process-loaded to registry-desired.

**Do not put `HARNESS_WRAPPER_VERSION` on `server.js`.** It is not a harness wrapper, and marking it
would make a marker lie in order to pass an allowlist, teaching a generic execution policy to confuse
two artifact classes. Use a registry-declared service-adapter executable with an install receipt and
content digest, carrying an explicit artifact kind and ABI. AIFY_ENV_BOUNDARY.md already admits
marker-only enrolment is forgeable; for this adapter it is not enough even on a local host once
several services can register executables.

### The pin is a third staleness, and it had no instrument

Found while checking the reviewer's own claim, 2026-08-29. aify-comms pinned `94b5716`; aify-wrapper's
HEAD was `bb56df5`, three commits later, and the top one was

> `fix(claude launcher): stop inheriting another session's child-session marker`

the fix for a defect the operator had been reporting all day. Its commit message ends *"NOT DEPLOYED —
this needs install.sh re-run and every wrapper relaunched"*. install.sh **was** re-run on that host.
It rendered the old template, because the pin still pointed before the fix.

It is a distinct cause from the bridge one: `child-env-hygiene.mjs` strips the marker on the spawn
path, which does nothing for a resident launcher a human starts from a shell that is already inside a
Claude Code session.

`wrapper-pin-freshness.mjs` and `the-wrapper-pin-is-not-behind-a-template-change.test.js` close it,
following `bridgeInstallVerdict`'s rule: being behind is not the fault, being behind by commits that
**touch the rendered templates** is. Quiet otherwise, because an alarm that fires on commits it has no
opinion about gets skimmed. No checkout is `unknown`, never a pass.

This does not replace loaded-fingerprint convergence. It is the cheap half that was missing entirely,
catchable in the checkout before anything is installed anywhere.

## Testing

Do not move the tests wholesale. aify-wrapper owns a **provider conformance kit**: a deterministic
driver command and event contract, per-harness protocol controls, no service vocabulary or imports, no
process ownership. Each service runs that kit against its pinned driver, plus its own adapter contract
tests. aify-env runs process-contract tests. A small cross-repo matrix runs at released identities.
That turns multiplication into one compatibility contract rather than N copied integration suites.

## Doctor split

- **aify-wrapper** answers launcher and driver package/ABI identity.
- **aify-env** answers desired and loaded process artifact identity, and custody.
- **aify-comms** answers adapter and service semantics, and reports the driver identity it loaded.
- Collectors display those reports. They do not reinterpret them.
