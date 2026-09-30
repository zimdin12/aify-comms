# P0: the contracts, frozen before any code

Companion to [2026-09-30-aify-env-owns-the-agents.md](2026-09-30-aify-env-owns-the-agents.md). The plan
review of 2026-09-30 (comms-senior-dev, R1-R7) found that the plan named the owners but not the
transitions between them. This file fixes those transitions. Every later phase implements one section
here and its witnesses; a phase that needs to change a rule here changes this file first, in its own
reviewed commit.

Source bases: aify-comms b8addd47, aify-env 0557d21, aify-wrapper 9f45a5a.

## C1. Identity and the file (R4)

**Agent id.** `^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$`, the service's own rule (`SAFE_NAME_RE`,
`service/api_core/validation.py:36`). Refused, never repaired: no trim, no case folding. Also refused:
a Windows reserved device name as the whole id or before its first dot, in any case (`CON`, `PRN`,
`AUX`, `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9`), and an id whose lower case equals an existing definition's
lower case (`x` is refused while `X.json` exists, on every OS, so the directory means the same thing on
Windows and Linux).

**Where.** `~/.aify/agent-definitions/<id>.json`, the directory overridable by
`AIFY_AGENT_DEFINITIONS_DIR` for tests. `~/.aify/agents/` stays aify-wrapper's lease directory.

**What a file may be.** A regular file (checked with `lstat`: a symlink, junction or directory entry is
an invalid entry, never followed), directly inside the directory, named `<id>.json` where `<id>` passes
the rule, whose body `agent.id` equals that name exactly. Anything else in the directory that ends in
`.json` and does not start with `.` is reported as an invalid entry. Names starting with `.` belong to
the store (`.collection.json`, `.lock`, `.trash/`).

**Schema v1.** A file is always written complete; a reader applies no defaults to a stored file, so
"absent" means invalid, not default.

| field | type | rule |
|---|---|---|
| `version` | 1 | anything else: invalid entry, file untouched |
| `revision` | integer >= 1 | this definition's own counter (C2) |
| `agent.id` | string | C1 rule, equals the filename |
| `agent.name` | string | 1-128 chars, no control characters |
| `agent.role` | string | `SAFE_NAME_RE` |
| `agent.harness` | `claude` \| `codex` \| `hermes` | the supported launchers; pi and opencode are not definable |
| `agent.mode` | `managed` \| `resident` | |
| `agent.workspace` | string | absolute path on this host, not checked for existence at write |
| `agent.model` | string | `""` means the harness's own default |
| `agent.effort` | string | `""` means the harness's own default |
| `agent.instructions` | string | at most 64 KiB |
| `agent.env` | object | the service's spawn-env rules exactly (`spawn_env.py`): names `[A-Za-z_][A-Za-z0-9_]{0,127}`, none starting `AIFY_` in any case, at most 32, string values of at most 4096 bytes |
| `agent.herdrSpace` | boolean | |
| `updatedAt` | ISO-8601 UTC | informational only; never used for ordering |
| `appliedRequest` | string or absent | the last service change request this revision applied (C4) |

**One schema, two languages.** aify-env (JavaScript) and aify-comms (Python) each validate it. Both
suites run one shared fixture, `test/fixtures/agent-definitions/cases.json` in aify-env (valid and
invalid bodies with the expected problem), which aify-comms reads as a sibling checkout the way its
other cross-repo tests do. A case one side accepts and the other refuses fails both suites.

**Fields left out, stated narrowly.** `systemPrompt`, `profile`, `channelIds` and the three policies
are stored in `spawn_specs`, serialized (`spawn_requests_io.py:88-96`) and copied by cold-start
(`dispatch_start.py:294-302`), but no consumer in the current launch path uses them. They are not in
v1. For a defined agent, a spawn's `spawn_specs` row is written with them empty; an undefined agent's
spawns keep today's behaviour exactly.

## C2. The store: one writer at a time (R4)

`DefinitionStore` (aify-env `lib/agent-definitions.mjs`) is the only code that writes the directory. A
test derives that from the source (every `fs` write call under `lib/` and `bin/` whose path can reach
the directory is in that module).

**Lock.** `.lock`, created with `wx`, holding `{pid, atMs, nonce}`. Held only for the duration of one
read-modify-write (milliseconds). A lock is taken over only when its holder pid is not running; a lock
held by a live pid is never taken, however old: the writer waits up to 5 s and then fails with the
lock's path and holder in the message. Takeover renames the lock away and puts it back if it changed
in between (the lease's protocol, `agent-lease.mjs:500-521`). A pid that was reused leaves a lock the
store will not take; the operator removes it, and the error says so.

**Write.** Under the lock: read the current file, check the caller's expected revision (compare-and-set:
a mismatch is a refusal naming both revisions), write `<id>.json.<pid>.tmp`, fsync, rename over the
file. On Windows a rename over a file another process has open can fail with EPERM/EBUSY: retried with
backoff for up to 2 s, then the write fails and the old file stands. A reader never sees a partial file
(it reads whole files replaced by rename).

**Collection record.** `.collection.json` `{version:1, storeId, revision, entries:{<id>: digest}}`.
`storeId` is a UUID made once, when the record is first created. `revision` is the collection's own
counter, advanced by one on every change of membership or content under the lock. `entries` holds the
sha-256 of each file's bytes as the store last wrote or adopted them.

**Hand edits are a second writer, and are adopted, not trusted blindly.** A snapshot (C3) compares every
file's digest with `entries`. A changed valid file is adopted: the store rewrites it in its own
formatting with `revision = old + 1` and the operator's values unchanged, and the collection revision
advances. A changed invalid file is left exactly as the operator wrote it and reported invalid.
A file that appeared by hand is adopted the same way (revision starts at its own value, or 1). A file
removed by hand is a removal. Adoption happens under the lock, so it cannot interleave with a store
write.

**Removal** moves the file to `.trash/<id>.<revision>.<requestId or "local">.json` (one rename), so a
removal is undoable by hand and a replayed removal request can be recognised (C4).

## C3. Snapshots: complete, ordered, fenced (R1)

**What a snapshot is.** Produced by `store.snapshot()` under the lock:

```
{ storeId, revision, complete: true|false,
  entries: [ {id, state: "valid",   revision, digest, definition, available, unavailableReason?}
           | {id, state: "invalid", problems: [...]} ] }
```

- `complete: false` when the directory could not be enumerated or any entry could not be read (EACCES,
  EIO). An incomplete snapshot is never pushed as membership: the plugin pushes nothing and reports the
  read failure in `aify-env doctor`.
- `available: false` when the definition is valid but its harness's launcher is not installed. The
  definition still exists; it is not startable.
- An empty directory that enumerates cleanly is `complete: true, entries: []`: an intentional empty set.

**The push.** `PUT /api/v1/environments/{envId}/agent-definitions`, body
`{bridgeId, machineId, storeId, revision, entries}` (a complete snapshot only).

**Admission.** Authenticated as every host call is (the service API key). Then fenced: `bridgeId` must
be the environment row's current accepted claimer (the same arbitration the heartbeat already makes,
`routers/environments.py:450-530`), and `machineId` must be the environment's machine. A superseded
aify-env's push is refused with the current claimer's id.

**Ordering.** The service keeps, per machine, `(storeId, revision, digest-of-snapshot)`.

| incoming vs held | outcome |
|---|---|
| same storeId, higher revision | applied |
| same storeId, same revision, same snapshot digest | 200, no change (a replay) |
| same storeId, same revision, different digest | 409: a store bug or a forged push; nothing applied |
| same storeId, lower revision | 409 stale; nothing applied |
| different storeId | applied, and becomes the machine's storeId (a recreated store, e.g. the directory was deleted); any later push from the old storeId is refused as stale |

Per-definition revisions are carried for C4 and for display; they never order membership.

**Application**, in one transaction:

- a `valid` entry whose id is unowned or owned by this machine: stored in `agent_definitions`
  (C5), owner = this machine;
- a `valid` entry whose id another machine owns: refused for that id, reported back in the response
  (`refused: [{id, reason: "defined on <machine>"}]`), and shown on the dashboard; the rest applies;
- an `invalid` entry: the previously accepted definition for that id (if any) is kept, marked
  `hostState: invalid` with the problems; never withdrawn;
- an id this machine owned that is absent from a complete snapshot: withdrawn (C6).

**Ownership (single definition owner for this tag).** The first accepted definition of an id takes
ownership for that machine. Ownership passes only when the owner withdraws the id (its complete
snapshot without it), or when the operator releases it explicitly:
`POST /api/v1/agent-definitions/{id}/release` (operator-authorized, `operator_authz.py`), for a host
that is gone. A withdrawal and a new owner are separate transactions; nothing is rewritten in
historical sessions. This is a limitation of this tag, not an answer to the multi-machine question.

**When the plugin pushes.** On plugin start, after every store write it makes, and every 60 s. A push
that fails is retried with the NEXT snapshot, never the old body, so a delayed retry cannot carry stale
membership.

**Witnesses.** Reversed delivery (S2 then S1: S1 refused, B still defined); stale retry after a removal
(refused, id stays withdrawn); publisher replacement (old bridgeId refused after a new claimer); an
invalid hand edit (previous definition kept, marked invalid); a directory read failure (nothing pushed,
nothing withdrawn); a launcher removed (definition kept, unavailable); an intentional empty snapshot
(all withdrawn). Each checks `agent_definitions` membership and owner after the step, not only the
HTTP status.

## C4. Change requests: compare-and-set, idempotent, fenced (R3)

**Row.** `definition_requests {id, agent_id, machine_id, expected_revision, patch, requested_by,
status, outcome, created_at, claimed_at, finished_at}`.

**Who may ask.** Operator-authorized only (`authorize_operator`), from the dashboard or the API. Agents
cannot request definition changes this tag.

**Patch.** A JSON merge patch over `agent` fields other than `id`: a key absent means unchanged; `null`
means "set to the schema's neutral value" (`""`, `{}`, or `true` for herdrSpace); anything else is the
new value, validated by C1 on the host before it is written. `{"remove": true}` is the only other shape.

**Admission at the service.** The agent must be defined, `machine_id` is its owner, and
`expected_revision` is the definition revision the service holds now. One pending request per agent:
a second is refused while the first is pending (409), so two edits from the same revision cannot both
queue.

**Claim.** `POST /api/v1/environments/{envId}/definition-requests/claim` with `bridgeId`: fenced to the
owning machine's current claimer, as C3. A request not claimed within 10 minutes expires.

**Apply, on the host.** `store.apply(request)` under the lock:

1. If the file's `appliedRequest` equals this request's id: already applied (a crash after commit, or a
   lost acknowledgement). Report `done` with the current revision; write nothing.
2. For a removal: if `.trash/` holds `<id>.*.<requestId>.json`, already applied; report `done`.
3. If the file's revision differs from `expected_revision`: `refused` ("changed on the host since you
   asked: expected R, now R'"). A stale removal of a recreated definition lands here.
4. Otherwise write the patched file with `revision + 1` and `appliedRequest = requestId` in the same
   atomic write (or move it to trash for a removal), then report `done` with the new revision, then push.

**Outcomes.** `pending -> claimed -> done | refused | expired`. `done` carries the resulting revision;
the dashboard shows "applied in aify-env, waiting for sync" until a snapshot at that revision or later
lands, then the new value. A request delivered after its agent's ownership moved is refused at the
claim (the claimer is not the owner).

**Witnesses.** Two edits from one revision (second refused at admission); a local edit then a queued
service edit (refused, file unchanged); crash after commit (replay reports done, no second write); lost
acknowledgement (same); stale removal after recreation (refused, file kept); delivery after transfer
(refused at claim).

## C5. What the service holds: desired, effective, live (R2, R6)

**Three kinds of field**, and who writes each:

| kind | fields | stored in | written by |
|---|---|---|---|
| desired | everything in C1 | `agent_definitions.body` | C3 push only |
| descriptive, applied at once | name, role, instructions, herdr_space | `agents` columns | C3 push (and nothing else, for a defined agent) |
| effective (what the running process is) | runtime, session_mode, cwd, model, runtime_config.effort, session_handle, capabilities, driver_state | `agents` columns | the paths that write them today: registration, running settlement, handle and lease routes |

Descriptive fields change nothing about delivery, so they apply as soon as a push lands; that is what
lets an operator fix a role or instructions without a restart. Execution fields are desired in
`agent_definitions` and effective in `agents`; **a desired change to them takes effect at the next
start** and never rewrites the effective columns of a live run. `execution_mode.py:37-83` keeps
reading the effective columns, so routing always describes the process that is actually running. The
dashboard shows "changes on next start" when desired and effective differ.

**Per-path dispositions for a defined agent** (undefined agents: every path unchanged):

| path | today | for a defined agent |
|---|---|---|
| registration, upsert branch (`agent_registration_writes.py:195`) | writes role, name, instructions, managed_by, and the effective fields | keeps the descriptive columns as the definition set them; writes the effective ones as today |
| registration, adopt branch (`:109`) and adopted-terminal handling | writes role, runtime | same rule as the upsert branch |
| running settlement (`running_spawn.py:73-118`) | copies role/name/instructions from the request, effective fields from request/spec | descriptive columns from the CURRENT definition row, not the request; effective fields from the request, as today |
| cold-start (`dispatch_start.py`) | role `coder`, name = id, spec copied from history | request and spec built from the current definition; its revision recorded on the request (C7) |
| restart / recreate (`session_restart.py`) | copies from `agents` | built from the current definition, revision recorded |
| direct spawn (`POST /spawn-requests`) | new spec from the body | refused for a defined agent (409, "defined in aify-env on <machine>; start it"); unchanged otherwise |
| environment assign (`POST /agents/{id}/environment`) | rewrites cwd/model/runtime/sessions | becomes a change request (C4) for workspace/model/harness; historical sessions are no longer rewritten for a defined agent |
| session-mode switch (`PATCH .../session-mode`) | rewrites mode and effective fields | becomes a change request for `mode` |
| apply-managed-defaults (`settings.py:50-71`) | bulk model/effort | skips defined agents and reports how many it skipped |
| pi flip (`pi_resident_flip.py`) | flips session_mode | not reachable: pi is not a definable harness (C1) |
| herdr-space (`PATCH .../herdr-space`) | writes `herdr_space` | becomes a change request |
| favorite, description, usage-source (`config.py:44-64`) | service-owned | unchanged (not definition fields; usage-source already preserves the rest of runtime_config) |
| rename | copies the row | refused (409, "rename it in aify-env": not in this tag) |
| remove (`DELETE /agents/{id}`) | tombstone, cancel runs, delete row | a removal request (C4); on `done`, the existing removal runs |

Guards sit in the transaction that writes, keyed on a row in `agent_definitions` read in that same
transaction, so a push racing a registration cannot interleave.

**Witnesses.** The role reset reproduced RED on today's code first. Then, for each row above, the
defined-agent control (descriptive fields preserved) and the undefined-agent control (unchanged
behaviour), plus the live controls: a defined agent's heartbeat, handle, lease and quota updates still
land.

## C6. Withdrawal is not removal (R3)

A withdrawn definition (absent from its owner's complete snapshot, or released by the operator):

- `agent_definitions` row deleted; `agents` row, sessions, messages and runs untouched;
- the agent is marked `definitionState: withdrawn`; a running worker keeps running (withdrawal is a
  desired-state change, not a stop);
- no cold-start, restart or spawn for it until it is defined again (409 naming the withdrawal), so it
  never falls back to an unmanaged cold-start;
- queued messages stay queued and are delivered to a live worker as today.

Defining it again (on any host, once unowned) restores `defined` with the new owner. Operator removal
of the agent itself is the separate, existing destructive path, reached through a removal request.

## C7. A start is bound to one definition revision (R2)

- A cold-start, restart or aify-env start of a defined agent records `definition_revision` and
  `store_id` on the spawn request, and builds the spawn (spec, role, name, workspace, model, effort,
  env) from that revision only.
- The launch payload (`GET /terminals/{id}/launch`) carries `definitionRevision` and `storeId`.
- **At the process-start boundary** (aify-env `terminal-controls.mjs` `startTerminal`, before the
  process starts; `claim.mjs` starts no process): the plugin reads the local definition. It refuses the
  control when the file is absent ("withdrawn on this host"), invalid, of another storeId, or at a
  different revision ("changed since this start was queued: R -> R'; start it again"), or when its
  harness does not match the launch's runtime. A refusal is reported on the control, as refusals are
  today, and the spawn fails with that reason. No worker starts from a mix of two revisions.
- An undefined agent's launch carries no revision, and the host does no check.

## C8. Plugins follow the registry without pretending the host stopped (R5)

Two operations, never one:

- **Host shutdown** (`plugin.stop()`, as today, `index.mjs:392-414`): offline beat with
  `heldTerminals: []`, because the host's workers go with it.
- **Configuration detach** (`plugin.detach()`, new): stop starting claim and control cycles, wait for
  the in-flight long-poll to return (at most its `waitMs`), process whatever it returned while still
  attached, and send nothing else. No offline beat, no empty `heldTerminals`.

**Policy when the registry changes:**

| change | plugin holds no workers | plugin holds workers |
|---|---|---|
| service added | start its plugin | n/a |
| service removed | `detach()` | **kept**: the plugin keeps serving its workers; `aify-env doctor` and the TUI show "registry change pending: N workers still held"; applied when the last one ends |
| endpoint changed | `detach()` the old plugin, start the new | **kept** on the old endpoint, same report; applied when the last worker ends |

No worker is killed or declared gone by a configuration change. A daemon restart applies everything at
once, as today.

**Witnesses** use the real aify-comms plugin with an injected fake transport and fake processes: a
held worker across a registry removal (still reported held, no offline beat); an endpoint change with a
claim in flight (the returned work is processed, then detach); a delayed reply from the old endpoint
after detach (ignored); and daemon shutdown still sending the offline beat.

## C9. The launcher and its definition (R7)

- `claude-aify`, `codex-aify`, `hermes-aify` with an agent id and no managed launch
  (`AIFY_MANAGED_VIA_WRAPPER` unset) read `~/.aify/agent-definitions/<id>.json` if it exists.
- **Missing file:** today's behaviour exactly.
- **Invalid file:** refused, exit 78, printing the problems; `--aify-ignore-definition` starts it the old
  way. An invalid file never silently becomes "missing".
- **Harness mismatch** (definition says hermes, launched as claude-aify): refused, exit 78, unless
  `--aify-ignore-definition`. Transcript swapping is the later tag.
- **Defaults it supplies:** role, model, effort. Precedence: flag > `HARNESS_*` > `AIFY_*` > definition >
  default. The definition file is never written by a launcher.
- **Workspace:** not applied to a resident; it runs where it is started, as today. What it actually ran
  with reaches the service through registration as the effective fields (C5).
- Managed launches take everything from the launch payload and do not read the file (the host already
  checked it, C7).

## C10. Import shows what it does not know (R7)

`aify-env agents import` is an operator-requested pull, the one exception to "the host pushes": it
asks each registered service offering the `agents` capability for this machine's agents.

- **Dry run by default.** For each id: which service(s) reported it, the value it would write for every
  field, and which fields the service does not report. `env` is one of those: it lives in historical
  `spawn_specs`, not in the agent record, and the spec to pick is already ambiguous
  (`spawn_spec_assignment.py:40-61`). Import writes `env: {}` and says so; it never presents an empty
  env as the agent's desired env.
- **Conflicts.** Two services reporting different values for one id: listed field by field; that id is
  not written until the operator picks, `--prefer <service>` or `--prefer <service>:<id>`. Registry
  order is not a choice.
- **Never overwrites** an existing definition. `--write` writes through `DefinitionStore`.
- Harness mapping from the service's runtime: `claude-code -> claude`, `codex -> codex`,
  `hermes -> hermes`; anything else is listed as not importable.

## C11. Mixed versions

- An aify-env without definitions and a new aify-comms: no pushes, so no defined agents; everything
  behaves as today.
- A new aify-env and an old aify-comms: the push route 404s; the plugin logs it once, keeps its other
  loops, and `aify-env doctor` reports "service does not accept definitions". Local list/show/validate
  work regardless.
- aify-dashboard: unchanged this tag.
