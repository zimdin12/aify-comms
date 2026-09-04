# Who tells the service what a host can do

**The question this settles:** the host-side `aify-comms` environment bridge exists because the
service could not reach the host. It can now. This records what that bridge actually does, where
each of those jobs belongs, and — the part that decides everything else — **which side dials which**.

Written 2026-08-29 from the code rather than from the design intent, because three separate readings
of this subsystem in one day were each confidently wrong.

## The bridge does five jobs, not one

`bootstrapEnvironmentBridge` in `mcp/stdio/server.js` starts four of them, and the fifth is the
terminal-control path that runs beside them:

| # | job | what it is |
|---|---|---|
| 1 | **advertise host capabilities** | which runtimes exist and in which modes, `terminalRuntimes`, `cwdRoots`, `machineId`, `pty`, launcher version and registry fingerprint |
| 2 | **heartbeat the environment row** | `POST /environments/heartbeat` — the transport for #1, and what makes an environment read `online` |
| 3 | **spawn loop** | poll the service for spawn requests, ask aify-env to launch a wrapper |
| 4 | **boot survivor sweep** | reap the previous generation's managed processes and tombstoned markers |
| 5 | **actuate terminal controls** | claim a control row and apply it — `comms_interrupt` is terminal-native Ctrl+C, written into the PTY aify-env owns |

Job 1 is the one with no other home today, and it is the reason the bridge is not merely a relay.
`heartbeatEnvironment` already asks aify-env for terminal health on every beat and forwards it —
"the difference between advertising what we can do and advertising what we could do before spawning
moved out of this process". That forwarding is the shape the whole component has been reduced to.

**What the bridge does NOT do**, and what two earlier readings of mine got wrong: it does not run
delivery loops or turn detection. The hermes wrapper does — it brings up the per-agent gateway host,
starts `hermes-managed-host.js run <agent>`, and then execs the real TUI into that PTY. Those stay
where they are. They are runtime mechanics and they belong next to the process.

## The direction decision

**The host dials the service. The service never dials the host.**

Measured rather than assumed: the service makes no outbound HTTP to a host at all. Its only `httpx`
is `service/containers/manager.py`, against the local Docker socket. Every host-to-service call —
registration, heartbeat, claim, turn boundaries — is outbound from the host.

That is not an accident to be cleaned up. `TARGET_ARCHITECTURE.md` says a machine that runs agents
installs aify-env and the launchers and **carries no copy of the service**, and the `api-exposure`
finding treats "costs remote environments" as a real price. A remote agent host may have no inbound
path at all. A design that requires the service to dial it works on one developer's laptop and
nowhere else.

`host.docker.internal:8802` answers 200 from inside the container today, and that fact is a trap: it
is true here because the host is local, and it will keep being true right up until the first remote
environment.

**So: aify-env dials the service and holds the connection. The service pushes intents down it.**

The mechanism already exists and has never had a client. `service/main.py` reads `agent_id` off the
`/ws` query string and hands it to `WSManager.connect`; `notify_agent` pushes one event to one id.
Nothing in this repo connects that way — every client is the dashboard's own socket with no id. It
was written for a consumer that did not exist yet. This is that consumer.

## Where each job goes

| job | home | why |
|---|---|---|
| 1 advertise | **aify-env** | it already reads the service registry, already reports `terminals.available`, and already identifies installed wrappers by contract marker — `allowlist.mjs`, "DERIVED, NOT LISTED". That marker read IS the detector: asking it of every `*-aify` on PATH answers "what was aify-wrapper installed for" with no dependency on aify-wrapper, which aify-env does not have — it declares none, and starts wrappers as files by path rather than importing them. |
| 2 heartbeat | **aify-env** | `POST /environments/heartbeat` is an existing endpoint taking an existing payload. Outbound from the host, so it survives a host with no inbound path. |
| 3 spawn | **service, over the held connection** | the service already owns the decision; today it writes a row and waits for a poller to notice. Pushing it down an open socket removes the poll interval and the claim. |
| 4 sweep | **aify-env** | it owns `runner.list()`, `runner.stop(id)`, and reports `processes[]` and `unknown[]`. Reaping its own previous generation is the definition of its job. |
| 5 actuate | **service, over the held connection** | the service already writes the intent; aify-env already owns the PTY and exposes `POST /processes/:id/input`. Today those two facts are joined by a host-side poller. |

Two fields of the environment row stay service-side because they are not host facts:
`registeredAt` is bookkeeping and `status` is a derivation.

**The seam to watch is `runtimes[].modes`.** Whether a harness is installed is a host fact. Whether
`managed-warm` is a mode it can be driven in is a statement about how a service drives it. It is
advertised from the host because it depends on the harness-and-wrapper pair, but it is the one field
two tiers can both hold an opinion about, and therefore the one that will drift first.

## `runtimes[]` is two kinds of fact, and only one of them is the host's

The seam I flagged as `runtimes[].modes` is wider than one field. `runtimeCapability()` builds each
row as:

    { runtime, modes: ["managed-warm"], available, unavailableReason,
      capabilities: { persistent, nativeResume, bridgeResume, cliAttach, interrupt,
                      streaming, tokenTelemetry, costTelemetry, contextReset } }

Ten of those fields are not host facts. Whether a runtime supports `nativeResume`, can be
`interrupt`ed, `streaming`s, or honours a `contextReset` is a statement about how a SERVICE drives
it — the same class of knowledge as `modes`. Only `available` and `unavailableReason` are answers a
host can give, and they come from a PATH probe.

An aify-env that produced this whole row would have to carry `normalizeRuntime`,
`runtimeLaunchAvailability`, `ENVIRONMENT_RUNTIME_IDS` and the capability table itself — aify-comms'
semantics, living in the tier whose README opens "Nothing here knows what a message is, what a
dispatch is, or whether an agent is thinking". That is the copy this whole document exists to avoid,
and it would be a large one.

**So the split is by who can answer, not by who happens to send:**

| fact | tier | why |
|---|---|---|
| which runtimes are installed, and the reason one is not | **aify-env** | a marker read of every `*-aify` on PATH, which fails closed: an unreadable file is absent, never present |
| terminal availability and its reason | **aify-env** | already in its `/health` |
| `modes` and the ten `capabilities` flags | **the service** | statements about how it drives a runtime, true of the runtime rather than of the host |

The service already owns the table; it would join its own capability rows to the availability the
host reports. That is strictly less code than today, where the table lives in aify-comms code running
ON the host and has to be shipped there and kept current — which is what `bridge-installed` and
`bridge-current` are for.

**This changes the Phase 1 payload.** aify-env sends availability, not `runtimes[]` as it stands
today, and the service enriches. Recorded here rather than discovered halfway through the advertiser,
where the tempting fix would have been to copy the table across and let two of them drift.

## What "available" means, corrected by building it

Writing the detector forced a question the existing probe had answered two different ways, and one of
them could not have worked.

**The wrapper is the fact, not the runtime.** A managed spawn is delegated to aify-env, which runs a
file only when it carries the harness contract marker. `omp.exe` carries none. So "Oh My Pi is on
PATH" is not "pi can be started here" — the tier that owns processes would refuse it — and the two
answers differ on this very host, where `omp` is installed and `pi-aify` is not.

Measured 2026-08-30 on the operator's machine, positive and negative control in the same walk:

| runtime | wrapper installed | runtime binary | what the bridge advertised |
|---|---|---|---|
| claude-code | yes | yes | available — correct |
| codex | yes | yes | available — correct |
| hermes | yes | yes | available — correct |
| pi | **no** | yes (`omp`) | **available — wrong**, probed the runtime |
| opencode | **no** | yes | **available — wrong**, probed nothing at all |

`opencode`'s branch returned a hardcoded `{ available: true, message: "OpenCode SDK available" }`,
describing a launch path its own adapter does not take: the map in `runtimes.js` records
`adapters/opencode.js -> "opencode-aify --resume <id>"`, and aify-wrapper ships no opencode template.
`managed-environment-sync.mjs` read exactly this field to decide what may be started, so both wrong
yeses became spawns that fail with no cause attached. **That module was deleted with the
environment bridge on 2026-09-04**; aify-env decides what may be started now, and reads the same
advertisement. The defect is worth keeping written down because it is a property of the FIELD, not
of the reader: a probe that answers `available` for a launch path its own adapter does not take
misleads whoever asks, and the question moved rather than closing.

Fixed in aify-comms so all five probe the file a spawn would run, gated by a test that runs the real
probe with a sealed PATH — the existing suite injected `availabilityFor` and so was green throughout.

**aify-env detects by reading the marker, not by probing for a runtime.** The earlier plan borrowed
`detectHarnesses`/`whichFrom` from aify-wrapper. aify-env has no dependencies and does not need them:
it already owns `allowlist.mjs`, whose rule is "a file may run if it carries the harness contract
marker", and asking that question of every `*-aify` on PATH answers "what was aify-wrapper installed
for" directly. Derived, not listed — a harness installed later is advertised with nobody editing
anything. Read, never run, because asking a pre-contract launcher what it is starts a runtime.

## The tenth field: `label`

The preservation fix covered nine fields a heartbeat could blank by omitting them. `label` was argued
out of that set on the grounds that `req.label or env_id` falls back to a real default rather than a
blank. True, and beside the point: on an UPDATE that default is the raw environment id, so a single
advertisement that says nothing about the label renames `Windows on StevenZ-L` to
`windows:StevenZ-L:default`. Measured against the running service rather than reasoned about, and
fixed the same way. The INSERT still uses the id, because a row being created has no prior name.

The advertiser is the caller that would have triggered it: aify-env has no idea what the operator
chose to call this machine, so it sends no label at all — and, for the same reason, no `cwdRoots`.
Which directories work may run in is the service's policy.

## Performance: advertise a fingerprint, not a payload

The detector walks `PATH` and reads every `*-aify` it finds. Recomputing that on every beat is the waste
worth removing — and it is the ONLY one worth removing here.

- aify-env detects capabilities at start and re-detects on a slow interval.
- A fingerprint over the capability fields says whether a re-detection changed anything.
- The payload is sent on every beat regardless, because it is a few hundred bytes.

**CORRECTED FROM THE FIRST DRAFT OF THIS DOCUMENT**, which said the heartbeat should carry only the
fingerprint and exchange the full document when the service holds a different one. That is a
NEGOTIATION — a service-side "do you already have this" round trip, a fingerprint the service must
store, and a fallback for when the two disagree — invented to save a payload smaller than the HTTP
headers around it. The cost is the PATH walk, not the bytes, so caching the detection is the whole
optimisation and the protocol stays one POST.

Worth recording because the first draft read as the more rigorous design and was the more expensive
one: a mechanism defending against a cost nobody had measured.

## Unity: one advertiser per host, not one per service

Today the advertiser is per service. A second service on the same host needs its own bridge to learn
the same facts, and the two can disagree about one machine.

With aify-env advertising, one daemon tells every registered service the same answer, and it already
knows whom to tell because it reads `~/.aify/services.json`. That is the environment tier's whole
purpose, applied to the last job still being done per-service.

It also collapses a version-skew surface. `bridge-installed` and `bridge-current` exist because
aify-comms ships host code whose staleness the service has to detect from a distance. A tier that
advertises its own version and fingerprint reports its own staleness by construction.

## The identity trap, and the one change that removes it

`environmentHeartbeatPayload` builds the id as `${kind}:${hostname}:default`, from `os.hostname()`
raw, with `AIFY_ENVIRONMENT_ID` as an override. The live row is `windows:StevenZ-L:default` while its
`machineId` is `win32:stevenz-l` — **raw casing in one field, lowercased in the other**, and this
repo already carries `scripts/normalize_machine_id_casing.py`, so that difference has cost something
before.

An advertiser that re-derives any of that differently does not update the environment. It creates a
SECOND row beside the real one, and both look plausible: same host, same runtimes, two ids. Nothing
errors, and the managed agents stay bound to whichever one the bridge wrote.

Reimplementing `environmentKind()`, `environmentOs()`, `MACHINE_ID` and the hostname rule in aify-env
is therefore the exact "two producers of one fact" shape these gates keep catching — it would agree
on the day it was written and drift on the first edit to either copy.

**The fix is to stop deriving it twice.** The id keys the SERVICE's own table, so the service should
compute it: aify-env sends `hostname`, `kind` and `os` as facts about the host, and the service
builds the id from them. One implementation, in the tier that owns the table, and no drift is
possible rather than merely unlikely.

That is a small contract change — `EnvironmentHeartbeat.id` is required today and supplied by the
caller — and it is worth making before the second advertiser exists rather than after. The bridge can
keep sending `id` unchanged during the cutover; a caller that sends `hostname`/`kind` and no `id` is
the new shape, and the service accepting both is what makes the transition boring.

**The precedent is already in the same function.** `terminalSupported` became an ARGUMENT precisely
because "since v0.6 Phase 8 this process is not the tier that answers it… an environment advertising
it was advertising a capability measured on a tier that no longer provides it". The environment id is
the same mistake one field over: a value computed by the tier that does not own it.

## The transition has two advertisers, and they can disagree

Found while reviewing the service-side change rather than while writing it, which is the only reason
it is here before it was a bug.

`runtimes`, `cwdRoots` and `terminalRuntimes` are written by whoever heartbeats last. During the
cutover both a bridge and aify-env can be up, and they do not compute those fields the same way — the
bridge probes runtimes its own way and asks aify-env only about terminal health, while aify-env would
answer from contract markers and aify-wrapper's detectors. Two writers of one fact, disagreeing, at
the interval of whichever beat lands last. The row would flap and the dashboard would flap with it.

**The cutover is the answer, not an overlap.** aify-env starts advertising and the bridge stops.

### SHIPPED 2026-08-30, and it needed two things this section did not predict

The rule is not "the bridge stops" but **"the bridge stands down when something else has taken over"**,
decided from a fact rather than from a flag on each side. aify-env reports `advertising` in the
`/health` the bridge already fetches on every beat, so the question costs no extra call, and it means
*I am posting host facts to at least one service* — armed AND with a target. A daemon that cannot read
its registry reports `false`, because standing down for nobody leaves a host described by no one, and
that is worse than two describing it.

Standing down requires a positive `true`. Absent, false, unreadable, an older daemon, delegation off:
all of them leave the bridge doing the job. `AIFY_ADVERTISE=0` hands it back explicitly.

**What each tier sends, after the split:**

| field | sent by | why not the other |
|---|---|---|
| `runtimes`, `terminalRuntimes`, `terminal`, `pty` | aify-env | it owns processes and reads the wrappers actually installed |
| `id`, `machineId`, `os`, `kind` | both | identity, and a cross-repo agreement test drives both builders over the same inputs |
| `label`, `cwdRoots` | the bridge | the operator's chosen name and the service's policy — aify-env sends neither, so nothing else would set them |
| `bridgeId`, `bridgeVersion`, launcher state, `bridgeStartedAt` | the bridge | supersession is arbitrated on these and aify-env is not a bridge |

**The two things building it exposed.**

*`terminal`, `pty` and `terminalRuntimes` are not columns.* They ride inside `metadata`, and
`next_metadata` replaces the blob — so the preservation rule the nine columns got did not reach them,
and a bridge standing down emptied the terminal answer instead of leaving it to the tier that owns it.
Found by an alternating-beat test going red on `terminalRuntimes` becoming `[]`, not by reading.
`HOST_OWNED_METADATA` is the symmetric partner of `BRIDGE_OWNED_METADATA`, which was already there.

*The service was not normalising runtime names on write.* This document says "send client names, let
the service normalise", and aify-env does send `claude`. The service stored it raw. Nothing broke —
both readers normalise both sides before comparing — but the row read `claude` in `runtimes` and
`claude-code` in `terminalRuntimes`, two lists on one row disagreeing about the same host.

**If an overlap is ever needed**, the rule that resolves it is already implied by the fix above: a
heartbeat carrying no `bridgeId` is the TIER speaking about the host, and one carrying a `bridgeId`
is a bridge speaking about itself. The service can prefer the tier's capabilities and let a bridge
update only what is its own. That is a small precedence rule, and it is worth writing only if an
overlap turns out to be necessary — building it first would be a mechanism defending a state nobody
has to be in.

What must NOT happen is the pair running with no rule at all, each overwriting the other, because
that failure looks like flapping hardware rather than like two components disagreeing.

## Who may ask — and why it has to be painless

**Operator requirement, 2026-08-29: nothing external may drive the agents, aify-env, or aify-comms,
and securing that must cost the installer nothing.** A local install wires its own keys; the install
instructions carry it; nobody hand-edits a secret.

Measured today, both tiers:

| tier | auth | reachable from |
|---|---|---|
| **aify-comms service** | a full middleware EXISTS -- `X-API-Key` header or `?api_key=`, constant-time compare, and `_authorize_websocket` for `/ws` -- and is **never installed**, because `API_KEY` is unset | published on `0.0.0.0` |
| **aify-env** | **none anywhere** | `const HOST = "127.0.0.1"`, hardcoded |

So the service is open to anything that can route to the port, and aify-env's only protection is that
it does not listen off-host.

**Loopback is a weaker boundary than it looks.** `host.docker.internal:8802` answers 200 from inside
the service container. A container is a different trust domain from "a process this operator started",
so on this machine every container can already ask aify-env to launch a wrapper. That is acceptable on
a single-user box and it is not a property to design against.

### What this costs the advertisement work

Nothing structural, and the ordering matters:

- **The advertisement direction is unchanged.** Host dials service, so the host never needs to accept
  a connection, which is the strongest position to be in. Adding a key to that call is one header.
- **Turning `API_KEY` on is blocked by ONE consumer, not by the design.** Every bridge is given the
  key at install; the DASHBOARD is not, so with a key set its `/api/v1` polls answer 401 while `/ws`
  stays exempt -- a page reporting a live connection over no data. Giving the dashboard a key is the
  whole unblock.
- **aify-env needs a shared secret before it is ever reached by anything but a local service**, and
  arguably before that, since the container already reaches it. Loopback plus a token is the belt and
  braces; loopback alone stops being sufficient the moment the service is expected to call in.

### Painless means the installer already knows how

The pieces exist and none of them is new machinery:

- `install.sh` already renders every launcher and already writes the endpoint into each one.
- `~/.aify/services.json` already declares `endpointEnv` -- the NAMES of the variables a launcher
  should read for a service's endpoint. A `keyEnv` beside it is the same shape, and the same reason:
  a name the bridge reads but the registry does not declare gets inherited from whatever launched
  the runtime.
- The service reads `API_KEY` from `.env` through the ordinary config path.

So the install flow is: generate a key once if absent, write it to `.env`, bake it into every
rendered launcher and into the dashboard's configuration, and declare its variable name in the
registry so a second service can do the same without inventing a convention. Re-running the installer
must REUSE the existing key rather than rotating it, for the same reason `installed-endpoint.sh`
exists -- an update that silently changes what the host already chose is the failure that script was
written to stop.

**This is a decision the operator has now taken, so it is no longer the open item it was in the
weak-points list.** What remains is ordinary work, and it is sequenced AFTER the advertiser: a key
that arrives before every caller has one turns a working fleet into 401s, and the advertiser is one
more caller.

## What this does not settle

**Whether aify-comms keeps a permanent host footprint.** The delivery loop's code ships in
`~/.aify-comms` and is started by the wrapper. Retiring the bridge does not retire that directory.
Three homes, each with a real cost:

- **leave it** — works today; every agent host needs `~/.aify-comms` kept in sync, which is exactly
  the `bridge-installed` / `bridge-current` pair already lived with;
- **move it into aify-wrapper** — matches "the wrapper owns turn detection", and teaches a generic
  launcher package one service's dispatch protocol, which is the coupling that package was split out
  to avoid;
- **move the deciding into the service** — the service pushes, the wrapper keeps turn detection
  because that is runtime mechanics. Removes the footprint and deletes the claim-arbitration family
  with it.

That is an operator decision, not a discovery, and Phase 1 below does not depend on it.

## Acceptance criteria

Not a smoke test. These are the four failure shapes this subsystem has actually produced, each with
a recorded incident, and a change that consolidates actuation must survive them:

1. **restart while a turn is in flight** — a rotation once adopted the terminal the restart was
   killing;
2. **interrupt racing turn-end** — the run completes between the intent and the actuation;
3. **a queued send landing mid-restart** — the dying sidecar claimed the brief;
4. **a stop arriving twice** — idempotence, and the reason `DELETE /processes/:id` already answers
   204 for a second caller.

The common cause in all four is two parties able to act on one agent. Consolidating to one actor
should delete the class rather than move it — and "should" is the word that was wrong three times in
one day, so each of these gets a test that fails before the change and passes after.

## Phases

- **Phase 0** — this document.
- **Phase 1** — aify-env advertises: it computes the environment payload and pushes
  `POST /environments/heartbeat` itself, fingerprinted. The service accepts a heartbeat whose source
  is the environment tier rather than a bridge. Independent of every open question above, and it
  removes job 1 and job 2 from the bridge.
- **Phase 2** — the held connection: aify-env dials `/ws`, the service pushes spawn and control
  intents down it, jobs 3 and 5 move, and the bridge has nothing left. Gated on Phase 1 landing green
  and on the four acceptance criteria above.
