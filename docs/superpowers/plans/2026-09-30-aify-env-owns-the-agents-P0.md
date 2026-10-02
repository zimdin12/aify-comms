# P0: the contracts, frozen before any code

Companion to [2026-09-30-aify-env-owns-the-agents.md](2026-09-30-aify-env-owns-the-agents.md). Two plan
reviews on 2026-09-30 (comms-senior-dev: R1-R7 on the plan, then five closures and four integration
points on this file at 22748507) found transitions the plan named but did not fix. This file fixes
them. Every later phase implements one section here and its witnesses; a phase that needs to change a
rule here changes this file first, in its own reviewed commit.

Source bases: aify-comms b8addd47, aify-env 0557d21 (bfacc7a on main since), aify-wrapper 9f45a5a.

## C1. Identity and the file

**Agent id.** Admitted by a FULL-STRING match of `[A-Za-z0-9][A-Za-z0-9._-]{0,127}`: in Python
`re.fullmatch` (the service's own `SAFE_NAME_RE` ends in `\Z` for this reason,
`service/api_core/validation.py:36`); in JavaScript `^...$` without the `m` flag. `agent\n` is refused
in both, and the shared fixture carries it. Refused, never repaired: no trim, no case folding. Also
refused: a Windows reserved device name as the whole id or before its first dot, in any case (`CON`,
`PRN`, `AUX`, `NUL`, `COM1`-`COM9`, `LPT1`-`LPT9`), and an id whose lower case equals an existing
definition's lower case (`x` is refused while `X.json` exists, on every OS, so the directory means the
same thing on Windows and Linux). That rule is per directory: across machines the service compares
ids exactly (C3 ownership), so its union can hold `X` from one host and `x` from another as two agents.
This is the only identifier authority; the plan points here.

**Where.** `~/.aify/agent-definitions/<id>.json`, the directory overridable by
`AIFY_AGENT_DEFINITIONS_DIR` for tests. `~/.aify/agents/` stays aify-wrapper's lease directory.

**What a file may be.** A regular file (checked with `lstat`: a symlink, junction or directory entry is
an invalid entry, never followed), directly inside the directory, named `<id>.json` where `<id>` passes
the rule, whose body `agent.id` equals that name exactly. Anything else in the directory that ends in
`.json` and does not start with `.` is an invalid entry. Names starting with `.` belong to the store
(`.collection.json`, `.intent.json`, `.lock`, `.trash/`, `.recovered/`).

**Schema v1.** A file is always written complete; a reader applies no defaults to a stored file, so
"absent" means invalid, not default. That holds for every desired field in both populations below; the
store's four identity fields are the one qualified case.

| field | type | rule |
|---|---|---|
| `version` | 1 | anything else: invalid entry, file untouched |
| `incarnation` | integer >= 1 | which lifetime of this id this is (C2); assigned by the store, never reused |
| `revision` | integer >= 1 | this incarnation's own counter (C2) |
| `agent.id` | string | C1 rule, equals the filename |
| `agent.name` | string | 1-128 chars, no control characters |
| `agent.role` | string | the id rule |
| `agent.harness` | `claude` \| `codex` \| `hermes` | the supported launchers; pi and opencode are not definable |
| `agent.mode` | `managed` \| `resident` | |
| `agent.workspace` | string | absolute path on this host, not checked for existence at write |
| `agent.model` | string | `""` means the harness's own default |
| `agent.effort` | string | `""` means the harness's own default |
| `agent.instructions` | string | at most 64 KiB |
| `agent.env` | object | the service's spawn-env rules exactly (`spawn_env.py`): names full-match `[A-Za-z_][A-Za-z0-9_]{0,127}`, none starting `AIFY_` in any case, at most 32, string values of at most 4096 bytes |
| `agent.herdrSpace` | boolean | |
| `updatedAt` | ISO-8601 UTC | informational only; never used for ordering |
| `appliedRequest` | string or absent | the last service change request this revision applied (C4) |
| `operation` | string | the id of the store operation that last wrote this file: its commit receipt (C2) |

**Exact edges**, decided here because the two languages would otherwise decide them differently; the
fixture carries each:

- Every string must be well-formed Unicode: a lone surrogate is invalid (JavaScript and Python count
  and encode one differently). Lengths in characters are Unicode code points, so a 128-emoji name is
  valid in both. Byte limits are UTF-8 bytes: `instructions` at most 65,536.
- "No control characters" means Unicode category Cc: U+0000-U+001F and U+007F-U+009F.
- `agent.workspace` is absolute by one host-independent rule, since the service validates a file from
  any host: it starts with `/`, or a drive letter then `:\` or `:/`, or `\\server\share`.
- `agent.env` values carry no NUL either (`spawn_env.py` refuses one).
- `appliedRequest`, when present, is 1-128 code points with no control characters.
- `updatedAt` matches `YYYY-MM-DDTHH:MM:SS(.1-9 digits)?Z` as a full-string pattern in both languages.
  Calendar validity is not checked (`2026-13-45T99:99:99Z` passes), so no host date parser decides it.
- The bytes are strict UTF-8: a byte that does not decode is `file: not-utf8` (Node's default decoder
  would substitute U+FFFD and pass it). A byte-order mark is not JSON (`file: not-json`), nor are
  `NaN` and `Infinity`, which Python's parser would otherwise accept.
- `operation` is a lower-case UUID, what the store's `randomUUID()` writes.
- A key the table does not name, at the top level or in `agent`, is invalid (`unknown-field`), so a
  left-out field cannot come back in by hand.
- `incarnation`, `revision`, `operation` and `updatedAt` are the store's identity fields, and a file is
  validated as one of TWO POPULATIONS, which every fixture case names:
  - a **candidate** is what a person wrote, or a file waiting for adoption: those four are untrusted
    and do not decide its validity (`updatedAt` is still checked when present). Their absence supplies
    no default for any desired field. Adoption assigns identity from the ledger and writes a fresh
    receipt (C2);
  - a **normalized** definition is what the store writes and what a valid snapshot publishes: all four
    are required and exact (`incarnation: missing`, `revision: not-a-counter`, `operation: format`).
    The service admits only this population and never allocates a host identity by ignoring one.
  The fixture carries one desired body with its identity omitted and with it forged, in both
  populations: a valid candidate each time, a refused normalized file each time. In a snapshot the
  identity is the entry's own `incarnation` and `revision`; its `definition` is the `agent` object.
- A problem is `<field>: <code>`, the same string in both languages, from the vocabulary the fixture
  lists; a file's problems are sorted. A problem is always ASCII: an unknown key is named only when it
  matches `[A-Za-z0-9_.-]{1,64}`, otherwise the problem is `file: unknown-field` or
  `agent: unknown-field`, so no problem can carry text the two languages encode or sort apart.

**Numbers.** `incarnation`, `revision`, the ledger's `revision` and `nextIncarnation` are integers in
`[1, 2^53 - 1]` (JavaScript's safe integers). Both languages refuse a value outside that range, or not
an integer, as invalid; neither rounds or wraps one. THE RULE IS CHECKED ON THE TEXT: every number token
in a file must be a plain integer literal (`-?(0|[1-9][0-9]*)`) inside the safe range, else the file is
`file: non-integer-number` or `file: unsafe-integer`, whatever field holds it, and validation stops
there. A parsed value cannot prove what was written: JavaScript's `JSON.parse` reads
`9007199254740990.5` as a safe integer and `1.0` as `1`, while Python reads `1.0` as a float. `true` is
never a number (`version: unsupported`, `incarnation: not-a-counter`).

**One schema, two languages.** aify-env (JavaScript) and aify-comms (Python) each validate it. Both
suites run one shared fixture, `tests/fixtures/agent-definitions/cases.json` in aify-env (valid and
invalid bodies with the expected problems and the population each is judged in, plus cases given as
exact text or bytes for the number and encoding rules, `agent\n` and `agent\r\n` among the ids; its
generator computes the golden bytes with Python's own encoder), which aify-comms
reads as a sibling checkout the way its other cross-repo tests do. A case one side accepts and the
other refuses fails both suites. The fixture also carries GOLDEN CANONICAL VECTORS for C3: snapshot
inputs with their exact canonical bytes and sha-256, including non-ASCII field values, an `env` whose
keys arrive out of order (`__proto__` and `constructor` among them, which a naive object copy loses),
problems listed out of order, and the largest safe integers; both encoders must produce those bytes
exactly.

**Fields left out, stated narrowly.** `systemPrompt`, `profile`, `channelIds` and the three policies
are stored in `spawn_specs`, serialized (`spawn_requests_io.py:88-96`) and copied by cold-start
(`dispatch_start.py:294-302`), but no consumer in the current launch path uses them. They are not in
v1. For a defined agent, a spawn's `spawn_specs` row is written with them empty; an undefined agent's
spawns keep today's behaviour exactly.

## C2. The store: one writer, crash-recoverable

`DefinitionStore` (aify-env `lib/agent-definitions.mjs`) is the only code that writes the directory.
What the test checks, and no more: nothing the store module exports and no getter of a store instance
hands the path back (derived by calling them, so no relay can forward such an export, whatever import
grammar it uses), and no other module under `lib/` or `bin/` names the folder or its override variable.
What it does NOT check: a caller that recovers the path from a diagnostic the store returns (a
held-lock error carries `lockPath`; several messages name a file), or a name spelled some other way.
Diagnostics keep their paths because the operator needs them; that a caller does not write through
one is a review obligation on each new caller, not something this test proves. The borrowed lease pattern is NOT claimed as a transaction proof;
this section is the store's own contract.

**Ledger.** `.collection.json`:

```
{ version: 1, storeId, revision, nextIncarnation, snapshotDigest, lastOperation,
  ids: { <id>: { incarnation, revision, fileDigest } } }
```

- `lastOperation`: the id of the last operation whose ledger this is: the ledger's commit receipt.

- `storeId`: a UUID made once, when the ledger is first created.
- `revision`: the collection's counter; advances on every settled operation (committed or unknown) and every change of the
  semantic snapshot (C3).
- `nextIncarnation`: store-wide, only ever increases, so an incarnation is never reused, even for an id
  that was removed and made again.
- `ids`: for each live definition, its incarnation, revision and the sha-256 of its file as the store
  last wrote or adopted it.

**Lock.** `.lock`, created with `wx`, holding `{pid, atMs, nonce}`, held for one operation
(milliseconds). **The store never takes a lock over.** A lock that exists makes the caller wait up to 5 s
and then fail with the lock's path and holder. If the holder is not running, the error says so and
names the remedy: `aify-env agents unlock`, an operator command that removes the lock only when its
holder pid is not running and after telling the operator what it is about to remove. Exclusivity is
never inferred by the store itself. Every durable step below re-reads `.lock` first and aborts the
operation if its own nonce is no longer there; the intent record (below) makes that abort recoverable.

**One operation, four steps**, under the lock:

1. Write `.intent.json` (temp, fsync, rename): `{operation, op: "set"|"remove"|"observe", id,
   incarnation, revision, requestId?, before, fileDigest?, trashName?, ledgerAfter}`. `operation` is a
   fresh UUID. `before` is what the operation found: the file's digest, or `absent`. `ledgerAfter` is
   the complete ledger the operation commits, with `lastOperation = operation`.
2. Apply: for `set`, write `<id>.json.<pid>.<nonce>.tmp` with `operation` in its body, fsync, rename
   over `<id>.json`; for `remove`, rename `<id>.json` to `.trash/<trashName>`, whose name ends in the
   operation id (below).
3. Write `.collection.json` = `ledgerAfter` (temp, fsync, rename).
4. Delete `.intent.json`.

The file and the ledger each carry the operation id in the same atomic rename that commits them. That
receipt, not a digest, is what says a step ran: a digest that no longer matches may mean a hand edit
AFTER the step, not that the step never happened.

On POSIX the directory is fsynced after each rename; Windows has no directory fsync from Node, and
relies on NTFS metadata journaling for the rename. A rename over a file another process has open can
fail on Windows (EPERM, EBUSY): retried with backoff for up to 2 s, then the operation fails at that
step and recovery settles it.

**Recovery**, run under the lock before any other work, every time the store is opened. With an
intent present, it first reads `.recovered/<operation>.json`: when that record exists, the operation's
outcome is the one it records, and the arm that holds below only finishes the settlement. A ledger
receipt written by a settlement is bookkeeping, not evidence that the original step 2 ran. Otherwise
the first arm that holds decides:

| arm | evidence | outcome |
|---|---|---|
| 1 | the ledger's `lastOperation` is the intent's `operation` | COMMITTED through step 3: delete the intent |
| 2 | the file's body carries `operation` = the intent's, or `.trash/` holds the name ending in it | COMMITTED at step 2: write `ledgerAfter`, delete the intent |
| 3 | the current state is exactly the intent's `before` (the file's digest equals it, or the file is absent where `before` is `absent`) and no trash file carries the operation | OUTCOME UNKNOWN: settled forward (below), with no operator step |
| 4 | none of the above | RECOVERY CONFLICT (below) |

**Arm 3 is never NOT COMMITTED.** Restoring the before bytes (or deleting a new id's file) is an
allowed hand edit, and it erases the only receipt, so "step 2 never ran" and "step 2 ran, then the
operator put the before state back" leave identical bytes. Recovery records the one lineage true of
both: the operation's revision, or its incarnation, is spent, and the state on disk now is a later hand
edit.

1. Write `.recovered/<operation>.json` (temp, fsync, rename): the intent verbatim, `requestId`
   included, with `outcome: "unknown"`. A temp file the intent names, if one exists, is moved to
   `.recovered/<operation>.body.json`. Nothing is deleted.
2. Continue as arm 2: write `ledgerAfter`, delete the intent. A crash between the two reopens in arm 1,
   which deletes the intent; the outcome stays UNKNOWN, because the record is read first.
3. Adopt. Every open ends its recovery, intent or not, with the snapshot's adoption pass, under the
   lock and before any other operation, so a crash after step 2 still reaches this. It finds the file
   differing from the ledger and records it as the later state: a set's restored bytes become revision
   R+1 of the same incarnation, R being the revision the operation reserved; a new id whose file is
   absent is a hand removal, its incarnation spent; a removal whose file is back is a file made by
   hand, so a new id with a new incarnation.

A snapshot runs recovery and adoption before it reads anything, so no snapshot ever carries the
unknown operation's revision. A request replayed after this finds neither its `appliedRequest` nor its
trash name, and a pair that moved, so C4 refuses it at step 4; its id and outcome stay in
`.recovered/`. A real crash before step 2 costs one revision number or one incarnation, which is the
price of never inferring that a step did not run.

- An `observe` intent (C3: only the ledger changes) has no step 2: arm 1 or, failing it, arm 2's
  outcome (write `ledgerAfter`).
- In arm 2 a file hand-edited after step 2 keeps its operator's bytes: the ledger records the digest
  the operation wrote, so the next snapshot adopts the hand edit as its own later revision (the lineage
  is set R, then hand edit R+1, never folded into one).
- In every arm `nextIncarnation` ends at least one past any incarnation the intent allocated, so an
  incarnation handed to an operation is never reused, committed or not.

**RECOVERY CONFLICT** is the answer when the bytes cannot prove the outcome: a file hand-edited in a
way that removed its receipt, or deleted, or replaced by something that is neither the before nor the
after state. The store then:

- deletes nothing and overwrites nothing: the intent, the current file, any temp file named in the
  intent and the trash stay exactly as found;
- refuses every operation, and every snapshot is `complete: false`, so nothing is pushed and nothing
  is withdrawn;
- reports the conflict in `aify-env doctor` and `aify-env agents list`, showing the intent, the
  before and intended digests, and the current bytes;
- is settled only by the operator: `aify-env agents recover --as-committed` (write `ledgerAfter`; the
  current file stays and is adopted as a later revision if it differs) or `--as-not-committed`
  (discard the intent; the current file stays and is adopted as a hand edit). Either first writes the
  intent to `.recovered/<operation>.json` with the operator's choice as its `outcome`, and keeps the
  incarnation rule above and any request's `appliedRequest` exactly as found in the file.

**Outcomes.** Settling an operation, which is what recovery always does, is not the same as the
operation having committed. An operation's outcome is exactly one of:

- `committed`: step 4 ran, or recovery found the operation's own receipt (arm 1 or 2) and no
  `.recovered/` record for it;
- `unknown`: `.recovered/<operation>.json` records it (arm 3). The store's state is accounted for; the
  operation is not claimed to have applied;
- the operator's choice, as recorded in `.recovered/<operation>.json` (arm 4).

`.recovered/` carries the outcome of every interrupted operation that recovery could not prove, with
its request. A record there is created once and never rewritten, including by a later restart. A caller
that crashed mid-operation reads `.recovered/` first, then the ledger, or for a request its
`appliedRequest` or the trash name (C4). A snapshot always runs recovery first, so it never publishes a
half-applied operation.

**Incarnations and revisions.**

- A new id (store write, or a file that appeared by hand) gets `incarnation = nextIncarnation`,
  `revision = 1`, and `nextIncarnation` advances.
- A set on an existing id keeps its incarnation and advances its revision.
- A removal ends the incarnation; defining the id again starts a new one.
- A caller's compare-and-set is on the pair `(incarnation, revision)`.

**Hand edits are a second writer, adopted under the lock, never trusted for identity.** At every
snapshot, each file's digest is compared with the ledger:

- a changed file that is valid is adopted: rewritten in the store's formatting with the ledger's
  incarnation for that id and `revision + 1`. An `incarnation` or `revision` typed into the file by hand
  is ignored; the ledger is the authority;
- a file with no ledger entry (made by hand) is a new id: new incarnation, as above;
- a changed file that is invalid is left exactly as the operator wrote it and reported invalid; the
  ledger keeps the last good incarnation and revision;
- a file removed by hand is a removal (ledger entry dropped, collection revision advanced).

**Trash names are unique:** `.trash/<id>.<incarnation>.<revision>.<requestId or "local">.<operation>.json`,
so a removal is undoable by hand, a replayed removal request is recognisable (C4), and recovery can
find the operation that made it.

**Witnesses (P1).** Identifiers (every C1 case from the shared fixture); the golden canonical vectors;
case collision; symlink and junction entries; a torn write (crash between each pair of steps, with no
hand edit: the next open settles committed, or unknown and forward when the crash came before step 2,
and the ledger, file, trash and `.recovered/` agree; a crash inside the forward settlement, after the
record, after the ledger, and after the intent's deletion, each reads back `unknown` with the adopted
file and ledger; and, as the positive control, an ordinary crash after step 3 with no record reads back
`committed` from arm 1);
recovery with a hand edit, each reading back the ledger, file, intent, trash and returned outcome:
interrupted after the file rename and before the ledger, then a valid hand edit to `name` (arm 2: set
at R, hand edit adopted as R+1); interrupted after the ledger and before the intent's deletion, then
the same edit (arm 1, then adoption); the receipt removed by hand (arm 4, everything kept, snapshot
incomplete, then each `recover` choice); the file deleted before recovery (arm 4 when the ledger lacks
the receipt, arm 1 when it has it); the before bytes restored exactly after step 2, and the same
crash before step 2 with no edit (both arm 3, both ending at revision R+1 with the before body and
the same `.recovered/` record, so the two histories are settled identically); a new id created then
its file deleted before recovery, and the same id interrupted before step 2 (both arm 3: absent, its
incarnation spent and never reused); a removal whose file was moved back from the trash (arm 3: a new
incarnation); a request's set interrupted then restored (its `requestId` read back from `.recovered/`,
its replay refused at C4 step 4); two writers racing (the second waits, then sees
the first's revision); a lock whose holder is alive is never taken; a lock whose holder is gone is
reported with the unlock remedy and still not taken; `unlock` removes only a dead holder's lock; a
hand edit adopted; an invalid hand edit reported and left as written; remove then recreate the same id
gives a new incarnation; repeated local removals leave distinct trash files.

## C3. Snapshots: complete, ordered, fenced

**What a snapshot is.** Produced by `store.snapshot({installedHarnesses})` under the lock, after
recovery. `installedHarnesses` is passed in (the launcher scan the daemon already does), so the store
reads no service and no PATH itself.

```
{ storeId, revision, complete: true|false,
  entries: [ {id, state: "valid", incarnation, revision, definitionDigest, definition,
              available, unavailableReason?}
           | {id, state: "invalid", problems: [...]} ] }
```

- `complete: false` when the directory could not be enumerated or any entry could not be read (EACCES,
  EIO). An incomplete snapshot is never pushed as membership: the plugin pushes nothing and reports the
  read failure in `aify-env doctor`.
- `available: false` when the definition is valid but its harness's launcher is not installed. The
  definition still exists; it is not startable.
- An empty directory that enumerates cleanly is `complete: true, entries: []`: an intentional empty set.

**Every semantic change is ordered.** The snapshot's canonical digest covers everything the service
acts on, so a change in availability or validity advances the revision like a change in a file:

- canonical form: entries sorted by id by UTF-16 code unit, for EVERY entry. A valid id is ASCII, but
  an invalid entry's id is its filename and can hold any character, and there Python's `str` order
  (by code point) differs from JavaScript's: U+1F600 sorts before U+E000 by code unit and after it by
  code point. Python sorts by `id.encode("utf-16-be")`; the fixture carries that pair as a golden
  vector (found in review, 2026-10-01). Each entry's keys sorted; JSON with no whitespace; UTF-8;
- the fields in it: `id`, `state`, and for a valid entry `incarnation`, `revision`, `definitionDigest`
  (sha-256 of the canonical JSON of `agent`), `available`, `unavailableReason`; for an invalid entry
  `problems`, sorted;
- exactly: the digested value is the JSON array of those entries. Object keys are sorted at every
  depth (the `env` inside `agent` too). A valid entry that is available has no `unavailableReason`
  key; one that is not has `unavailableReason: "harness-not-installed"`. Strings are escaped as
  JavaScript's `JSON.stringify` and Python's `json.dumps(..., ensure_ascii=False)` both escape them:
  `"`, `\` and U+0000-U+001F only, with the `\b \f \n \r \t` short forms and `\u00xx` in lower case
  for the rest; everything else is raw UTF-8. Digests are lower-case hex;
- `snapshot()` computes that digest; if it differs from the ledger's `snapshotDigest`, it advances the
  collection revision and records the digest, through the four-step operation of C2 (op `observe`,
  whose step 2 is empty). The same state enumerated in another order gives the same digest, so no
  conflict arises from filesystem order.

**The push.** `PUT /api/v1/environments/{envId}/agent-definitions`, body
`{bridgeId, machineId, storeId, revision, snapshotDigest, entries}` (complete snapshots only).

**Admission.** Authenticated as every host call is (the service API key). Then fenced: `bridgeId` must
be the environment row's current accepted claimer (the heartbeat's own arbitration,
`routers/environments.py:450-530`), and `machineId` the environment's machine. A superseded aify-env's
push is refused with the current claimer's id. The service recomputes the canonical digest from
`entries` and refuses a body whose digest does not match `snapshotDigest`.

**Ordering.** The service keeps, per machine, the current `(storeId, revision, snapshotDigest)` and
the set of RETIRED store ids.

| incoming | outcome |
|---|---|
| current storeId, higher revision | applied |
| current storeId, same revision, same digest | 200, no change (a replay) |
| current storeId, same revision, different digest | 409: a store bug or a forged push; nothing applied |
| current storeId, lower revision | 409 stale; nothing applied |
| a retired storeId | 409 retired; nothing applied |
| a storeId never seen for this machine | applied; the previous current storeId is retired, permanently |
| no current storeId yet | applied; becomes current |

A store id is identity, not order, so order across stores comes from retirement: once B has replaced
A, A can never be accepted again, however late its push arrives. Returning a machine to an older store
(two definition directories used in turn) is an operator action:
`POST /api/v1/environments/{envId}/definition-store/reset` (operator-authorized) clears the retired set
for that machine. A push already in flight is decided by this table when it arrives; the plugin's
"retry with the next snapshot" only means it never re-sends an old body itself.

**The machine is `machineId`, and two stores must never share one silently.** aify-env derives it as
`<platform, or wsl>:<host>` lower-cased (`lib/advertise.mjs` `machineIdFor`), so the Windows and WSL
installs on one PC are `win32:stevenz-l` and `wsl:stevenz-l`: two machines, two stores, no retirement.
`AIFY_MACHINE_ID` replaces only the host part, so it keeps them apart too. What makes two stores one
machine is two installs on the SAME platform and host with their own definition directories: two
Windows user profiles (each with its own `~/.aify`), or two daemons with different
`AIFY_AGENT_DEFINITIONS_DIR`. The table above would then let each retire the other (raised and
corrected by dashboard-manager, 2026-09-30). So a retired store's
push is refused with the store that retired it and when, and a retired store that keeps pushing is
reported in `aify-env doctor` and on the dashboard as "two stores claim machine <id>", never only
logged.

**Application**, in one transaction:

- a `valid` entry whose id is unowned or owned by this machine: stored in `agent_definitions`
  (C5), owner = this machine, with its incarnation and revision; `available` recorded;
- a `valid` entry whose id another machine owns: refused for that id, reported back in the response
  (`refused: [{id, reason: "defined on <machine>"}]`), and shown on the dashboard; the rest applies;
- an `invalid` entry: the previously accepted definition for that id (if any) is kept, marked
  `hostState: invalid` with the problems; never withdrawn;
- an id this machine owned that is absent from the snapshot: withdrawn (C6).

**Ownership (single definition owner for this tag).** The first accepted definition of an id takes
ownership for that machine. Ownership passes only when the owner withdraws the id, or when the operator
releases it for a host that is gone: `POST /api/v1/agent-definitions/{id}/release`
(operator-authorized). A withdrawal and a new owner are separate transactions; nothing is rewritten in
historical sessions. This is a limitation of this tag, not an answer to the multi-machine question.

**When the plugin pushes.** On plugin start, after every store operation it makes, and every 60 s.

**Settled while building P3a** (2026-10-01):

- **A newly defined id gets its `agents` row at the push.** The roster, the dashboard's mirror,
  role-addressed sends and the agent-level start all key on that row, so a defined agent with no row
  would be invisible to all of them. The row takes the descriptive fields and, since nothing has run
  yet, its initial effective fields (runtime from the harness, session mode, cwd, model, effort) from
  the definition. For an existing row, a push writes the descriptive columns and
  `definition_state` only (C5). C7's first-start witness therefore reads "no agent row before the
  push, and no session".
- **The fence fails closed.** An environment with no recorded claimer refuses a push. The spawn claim
  lets anyone through in that state; a push can withdraw every agent a machine owns.
- **An invalid entry for an id this machine has no definition of** is reported in the response
  (`invalid`) and stored nowhere: there is no last good definition to keep.
- **The canonical form escapes a lone surrogate** as `\udxxx`, lower case, as JavaScript does. Python
  would otherwise fail to encode it, or digest it differently.
- **The service checks that each valid entry's `definition` digests to its `definitionDigest`**, so a
  push cannot carry a body that differs from the digest the snapshot was ordered by.
- **The service admits a valid entry by C1 itself** (review of P3a, R2). Its id, counters and
  complete `agent` object are checked by `definition_schema.py`, a port of aify-env's validator held
  to aify-env's fixture case for case, before the digest is compared. Nothing is defaulted. Only what
  the wire carries is checked: the file's `version`, `operation`, `updatedAt` and byte rules stay with
  the host.
- **A replay gets its revision's answer again** (R4). Each applied push stores what it left
  unresolved (refused ids, invalid entries, kept definitions). A replay returns that, with each refused
  id judged as it stands now and read-only. An id that has since become free reads `free since this
  revision was applied; a fresh revision defines it`. A replay never applies anything, so a release
  never hands an id to whichever machine replays first. A release does not advance any host's
  revision: the refused machine takes the id with its next fresh revision. P4 decides when aify-env
  publishes one on seeing that reason.
- **An outcome nobody recorded stays unknown** (review of a171e8a3, N1). `definition_stores.outcome`
  is added by a migration, so a database P3a created gains it at init. It arrives NULL, meaning not
  recorded, so the replay of a revision applied before it answers `outcomeRecorded: false` with empty
  lists. Those lists mean unknown, not resolved. Every replay carries `outcomeRecorded`, and the host's
  next fresh revision records an outcome. No deployed database holds these tables: the 0.8 branch has
  only ever run in tests and review scratch.
- **Every check that guards a write is read inside that write's transaction** (R1 for release, N2 for
  the direct spawn), or is part of the writing statement itself (the P3b description guard).
- **A release names the machine it releases from** (R1). Its owner is read inside the release's write
  transaction. If custody moved, the release refuses (409, naming the current owner) and changes
  nothing.
- **A push refuses, per entry, what registration refuses**: an operator name (`dashboard`,
  `operator`) and an id the operator removed (tombstoned). The rest of the snapshot applies, as with
  an id another machine owns.
- **A defined agent cannot be renamed on the service.** Its id is its host's file name, so a rename
  here would split it from its definition, and the next push would define the old id again. The 409
  names the host-side way, which is a replacement and not a rename: define the new id with the fields
  wanted (`aify-env agents set`), then withdraw the old (`aify-env agents remove`). History and any
  running worker stay under the old id.
- **No event of its own.** The dashboard learns of a push from the change feed, which reports the
  tables a commit wrote; the agents slice names `agent_definitions`.
- **The agent list and detail carry `definition`** (state, owner machine, store, incarnation, revision,
  host state, availability), and every serialized agent carries `definitionState` (D13).

**Witnesses (P1 for the store half, P3 for the service half).** Reversed delivery (S2 then S1: S1
refused, B still defined); stale retry after a removal (refused, id stays withdrawn); A, then B from a
recreated store, then a delayed A (A refused as retired, B current); publisher replacement (old
bridgeId refused); unchanged definition bytes through available, unavailable, available (each an
ordered revision, no withdrawal, no conflict); valid, invalid, repaired (previous kept while invalid,
no withdrawal); the same state enumerated in two orders (same digest); a directory read failure
(nothing pushed, nothing withdrawn); an intentional empty snapshot (all withdrawn); two stores under
one machineId alternating, made the way it happens (one platform and host, two definition
directories): the second retires the first, the first is refused naming the second, and the doctor
row names both. Each checks
`agent_definitions` membership and owner after the step, not only the HTTP status.

## C4. Change requests: compare-and-set on lifetime and revision, idempotent, fenced

**Row.** `definition_requests {id, agent_id, machine_id, store_id, expected_incarnation,
expected_revision, patch, requested_by, status, outcome, result_incarnation, result_revision,
created_at, claimed_at, finished_at}`.

**Who may ask.** Operator-authorized only (`authorize_operator`), from the dashboard or the API. Agents
cannot request definition changes this tag.

**Patch.** A JSON merge patch over `agent` fields other than `id`: a key absent means unchanged; `null`
means "set to the schema's neutral value" (`""`, `{}`, or `true` for herdrSpace); anything else is the
new value, validated by C1 on the host before it is written. `{"remove": true}` is the only other shape.

**Admission at the service.** The agent must be defined; `machine_id` and `store_id` are its owner and
that machine's current store; `expected_incarnation` and `expected_revision` are what the service holds
now. One pending request per agent: a second is refused while the first is pending (409).

**Claim.** `POST /api/v1/environments/{envId}/definition-requests/claim` with `bridgeId`: fenced to the
owning machine's current claimer, as C3, and only requests whose `store_id` is that machine's current
store. A request not claimed within 10 minutes expires; one whose store id was retired meanwhile is
refused at the claim.

**Apply, on the host**, as one C2 operation:

1. The request's store id is not this store's: `refused` ("made for another store").
2. The file's `appliedRequest` equals this request's id: already applied (a crash after commit, or a
   lost acknowledgement): `done` with the current incarnation and revision; nothing written.
3. For a removal: `.trash/` holds a file named `<id>.<incarnation>.<revision>.<requestId>.<operation>.json` for this request: already applied, `done`.
4. The file's `(incarnation, revision)` differs from the expected pair: `refused` ("changed on the host
   since you asked"). A removal made for an earlier lifetime of a recreated id lands here even when the
   revision numbers are equal, because the incarnation differs.
5. Otherwise commit (a set with `revision + 1` and `appliedRequest = requestId`, or a removal to
   `.trash/<id>.<incarnation>.<revision>.<requestId>.<operation>.json`), report `done` with the result pair, then
   push.

**Outcomes.** `pending -> claimed -> done | refused | expired`. `done` carries the result pair; the
dashboard shows "applied in aify-env, waiting for sync" until a snapshot carrying that pair or later
lands, then the new value.

**The service's own consequences are fenced too.** A removal request's `done` runs the existing
destructive agent removal ONLY when, in that transaction, the service still holds the definition at
the request's expected `(store_id, incarnation)` or already saw it withdrawn by that store at exactly
that lifetime, and no other machine owns the id. A delayed `done` that finds a different incarnation,
store or owner records the host outcome and removes nothing, and says so on the request.

**Settled while building P3c** (2026-10-01):

- **Routes.**
  - Operator: `POST /agent-definitions/{id}/requests` with `{patch, requestedBy}`. A patch is an object
    of agent fields other than `id`, or exactly `{"remove": true}`, where `true` is the boolean. The
    service checks the shape only; the values are the host's to judge by C1.
  - `GET` on the same path lists the agent's requests.
  - Host: `POST /environments/{env}/definition-requests/claim` with `{bridgeId, machineId}`, and
    `POST /environments/{env}/definition-requests/{id}/result` with `{bridgeId, machineId, status:
    done|refused, outcome, resultIncarnation, resultRevision}`. Both are fenced as a push is.
- **Expiry needs no sweep.** A request stores `expires_at`. Every read of the queue (admission, claim,
  list) expires pending requests past it first.
- **A claimed request that was never reported is handed out again** on the next claim. The host's
  apply is idempotent (step 2), so a host that crashed between claim and report finishes it.
- **Refused at the claim** (the service writes the reason): made for a store the machine has since
  replaced; the definition withdrawn; the definition moved to another machine.
- **A report is idempotent and final.** The same status and result pair again answers 200 and changes
  nothing (a lost acknowledgement). Any other report on a finished request is 409.
- **The removal fence** compares (machine, store, incarnation) with what the request was made
  against. It reads the definition still held, or the record of the store that withdrew it
  (`agents.definition_withdrawn`, written at withdrawal; a release writes none). It is asked before a
  managed worker is told to stop, and again inside the transaction that deletes. A refusal at either
  asking removes nothing, and the request's `consequence` says why. The removal itself
  is the DELETE route's own (`api_core/agent_remove.py`), moved out of the route so both use it.

- **A host reports only what it claimed** (review of 12766276). A report on a pending request is 409:
  the claim is where an undeliverable request is refused. A report also expires what is due first.
- **The service's own consequence is owed until it is settled.** The report that records a `done`
  removal sets `definition_requests.consequence` to `pending` in the same write. Whichever report finds
  it pending runs the removal and settles it as `removed` or `nothing removed: <why>`. So a crash after
  the host's answer is recorded is finished by the next report, and a settled one is never run again:
  a replay changes no byte. The host's own `outcome` is kept as the host wrote it. A tombstoned agent
  with no row counts as already removed, because the row that recorded its withdrawal is the row the
  removal deleted. `consequence` is added by a migration, since 12766276 made the table without it.
- **Settled once** (review of c8029614). Two reports can both read `pending`, and custody can move
  between their fences, so the settle writes only while the consequence is still `pending`, and the
  removal's fence first asks, inside the deciding transaction, whether another report has settled it. A
  report that waited therefore neither rewrites the record nor runs a removal the record says was not
  made.
- **A receipt from before the column keeps what it recorded** (review of c8029614, N5). 12766276
  committed the host's `done` before removing and recorded only a refusal, as a trailing
  `[service: <why>]` note on the outcome. So the migration reads, per done removal: a note is the
  refusal made (`nothing removed: <why>` from the first trailing note), kept even when a fence asked now
  would allow; no note with the row gone and a tombstone is `removed`; no note with the agent still
  there recorded nothing, so it is owed (`pending`) and the next report asks the fence. A host outcome
  that itself ended in the note's form cannot be told apart in such a receipt.
- **The fence and the stop are one transaction.** With a fence, the removal's first asking and the
  stop it writes share one BEGIN IMMEDIATE, so a stop never reaches another owner's worker. The
  second asking, inside the deleting transaction, exists only after a managed worker's stop
  committed. Without a stop, the first asking covers the delete in the same transaction.
- **The edit routes queue for a defined agent** (C5's table):
  - herdr-space queues `{herdrSpace}`. Its guard is the UPDATE's own WHERE, so no check precedes the
    write.
  - session-mode queues `{mode}`.
  - Environment assign queues the harness, workspace, model and effort it names. It refuses an
    environment on another machine (409) and a runtime no definition can name (422), and queues
    nothing when nothing changes. The patch depends on the owner, so the owner is read, the
    environment judged against it and the request queued in one write transaction
    (`assignment_for_its_host`; review of c8029614, N3).
  - DELETE queues `{remove: true}`, its fence refusing a defined agent before any stop.
  - Each records its actor through `recorded_operator_actor`, so with an operator key only the
    operator queues a change.
  - The session-mode and environment checks precede their writes. An agent defined in between is
    edited as before; those writes touch only effective columns, and its definition governs its next
    start (C7).

**Witnesses (P3/P4).** Two edits from one pair (second refused at admission); a local edit then a
queued service edit (refused, file unchanged); crash after commit (replay reports done, no second
write); lost acknowledgement (same); a removal queued for incarnation 1 at revision 1, the id removed
and recreated locally at incarnation 2 revision 1 (refused at step 4, file kept); a delayed `done` for
that old removal (the service removes nothing); delivery after transfer or store retirement (refused
at the claim).

## C5. What the service holds: desired, descriptive, effective

**Three kinds of field**, and who writes each:

| kind | fields | stored in | written by |
|---|---|---|---|
| desired | everything in C1 | `agent_definitions.body` | C3 push only |
| descriptive, applied at once | name, role, instructions, herdr_space | `agents` columns | C3 push (and nothing else, for a defined agent) |
| effective (what the running process is) | runtime, session_mode, cwd, model, runtime_config.effort, session_handle, capabilities, driver_state | `agents` columns | the paths that write them today: registration, running settlement, handle and lease routes |

Descriptive fields apply as soon as a push lands. They do not change which execution backend or driver
owns a run, but they are not inert: `role` selects recipients of role-addressed messages
(`dispatch_messages/shared.py:168-173`), so a role change re-routes future `toRole` sends at once, which
is what an operator changing a role means. Execution fields are desired in `agent_definitions` and
effective in `agents`; **a desired change to them takes effect at the next start** and never rewrites
the effective columns of a live run. `execution_mode.py:37-83` keeps reading the effective columns, so
routing to a backend always describes the process that is actually running. The dashboard shows
"changes on next start" when desired and effective differ.

**Per-path dispositions for a defined agent** (undefined agents: every path unchanged):

| path | today | for a defined agent |
|---|---|---|
| registration, upsert branch (`agent_registration_writes.py:195`) | writes role, name, instructions, managed_by, and the effective fields | keeps the descriptive columns as the definition set them; writes the effective ones as today |
| registration, adopt branch (`:109`) and adopted-terminal handling | writes role, runtime | same rule as the upsert branch |
| running settlement (`running_spawn.py:73-118`) | copies role/name/instructions from the request, effective fields from request/spec | descriptive columns from the CURRENT definition row, not the request; effective fields from the request, as today |
| cold-start (`dispatch_start.py`) | role `coder`, name = id, spec copied from history | request and spec built from the current definition; its store id, incarnation and revision recorded on the request (C7) |
| restart / recreate (`session_restart.py`) | copies from `agents` | built from the current definition, the same three recorded |
| direct spawn (`POST /spawn-requests`) | new spec from the body | refused for a defined agent (409, "defined in aify-env on <machine>; start it"); unchanged otherwise |
| environment assign (`POST /agents/{id}/environment`) | rewrites cwd/model/runtime/sessions | becomes a change request (C4) for workspace/model/harness; historical sessions are no longer rewritten for a defined agent |
| session-mode switch (`PATCH .../session-mode`) | rewrites mode and effective fields | becomes a change request for `mode` |
| apply-managed-defaults (`settings.py:50-71`) | bulk model/effort | skips defined agents and reports how many it skipped |
| pi flip (`pi_resident_flip.py`) | flips session_mode | not reachable: pi is not a definable harness (C1) |
| herdr-space (`PATCH .../herdr-space`) | writes `herdr_space` | becomes a change request |
| favorite, description, usage-source (`config.py:44-64`) | service-owned | unchanged (not definition fields; usage-source already preserves the rest of runtime_config) |
| rename | copies the row | refused (409, "rename it in aify-env": not in this tag) |
| remove (`DELETE /agents/{id}`) | tombstone, cancel runs, delete row | a removal request (C4); on a fenced `done`, the existing removal runs |

Guards sit in the transaction that writes, keyed on a row in `agent_definitions` read in that same
transaction, so a push racing a registration cannot interleave.

**Settled while building P3b** (2026-10-01):

- **The role reset was older than definitions.** Every cold start wrote `coder` and the id, defined
  or not, so it was fixed for every agent in 0.7.8 (the cold start reads the agent's row). In 0.8 the
  guard below covers what 0.7.8 could not: a worker registering `name: <id>` no longer resets a
  defined agent's name.
- **One guard, inside each writing statement.** `definition_guard.kept_when_defined` makes a SET
  clause that keeps the column when `agent_definitions` holds the agent, read by that same statement.
  It covers role, name and instructions in the registration upsert, the adopt branch and running
  settlement. `herdr_space` is written by none of them.
- **The direct spawn refusal** reads `Agent "<id>" is defined in aify-env on <machine>; start it, and
  its spawn is built from that definition`.
- **apply-managed-defaults** leaves defined agents and their spawn specs as they are, and returns
  `skippedDefined`. The dashboard's toast names the count.

**Witnesses (P3).** The role reset reproduced RED on today's code first. Then, for each row above, the
defined-agent control (descriptive fields preserved) and the undefined-agent control (unchanged
behaviour), plus the live controls: a defined agent's heartbeat, handle, lease and quota updates still
land.

## C6. Withdrawal is not removal

A withdrawn definition (absent from its owner's complete snapshot, or released by the operator):

- `agent_definitions` row deleted; `agents` row, sessions, messages and runs untouched;
- the agent is marked `definitionState: withdrawn`; a running worker keeps running (withdrawal is a
  desired-state change, not a stop);
- no cold-start, restart or spawn for it until it is defined again (409 naming the withdrawal), so it
  never falls back to an unmanaged cold-start;
- queued messages stay queued and are delivered to a live worker as today.

Defining it again (on any host, once unowned) restores `defined` with the new owner. Operator removal
of the agent itself is the separate, existing destructive path, reached through a removal request.

## C7. A start is bound to one definition lifetime and revision

- A start of a defined agent never needs a prior session. The service's start for a defined agent
  (the agent-level start, `POST /agents/{id}/control` with `start`) builds a spawn request from the
  current definition whether or not the agent has any history; that is how a newly defined id first
  runs. aify-env's start for a defined agent calls that route; its session-based restart
  (`restartTargetFor`, `agent-starter.mjs:130-137`) stays for undefined agents only.
- The spawn request records `definition_store_id`, `definition_incarnation` and `definition_revision`,
  and builds the spawn (spec, role, name, workspace, model, effort, env) from that revision only.
- The launch payload (`GET /terminals/{id}/launch`) carries the same three.
- **At the process-start boundary** (aify-env `terminal-controls.mjs` `startTerminal`, before the
  process starts; `claim.mjs` starts no process): the plugin reads the local definition. It refuses the
  control when the file is absent ("withdrawn on this host"), invalid, of another store or incarnation,
  or at a different revision ("changed since this start was queued: R -> R'; start it again"), or when
  its harness does not match the launch's runtime. A refusal is reported on the control, as refusals are
  today, and the spawn fails with that reason. No worker starts from a mix of two revisions.
- A `resident` definition is never started by the service: residents are started by the operator
  through a launcher (C9). A definition changed from managed to resident while a managed worker runs
  leaves that worker running; once it stops, the agent is no longer offered for a managed start.
- An undefined agent's launch carries none of the three, and the host does no check.

**Settled while building P3d** (2026-10-01):

- **One guarded insert.** A service start is the cold start (agent-level start, a message, a channel
  post, the queued-run backstop, a spec-less restart), a restart or recreate of a session with a spec
  (built from the definition when there is one, from the session's spec when the agent was never
  defined), or the direct spawn. Each reads `definition_start.start_binding`, and each writes its spawn
  request through `insert_spawn_request`, whose WHERE asks the same question again. A push or
  withdrawal between the read and the write therefore inserts nothing ("its definition changed while
  this start was being made; start it again"). Some callers hold the write lock and some do not, and the
  guard does not depend on which. An earlier version of this note said "two inserts", while the
  undefined old-spec restart and the direct spawn still wrote their own (review of 8de83233, N6).
  `test_every_start_goes_through_the_guarded_insert.py` holds this to a stated grammar. It parses every
  product file under `service/` and reads each string literal from the syntax tree, so adjacent literals
  are joined and an f-string's literal parts count. A literal inserting into `spawn_requests`, bare or
  quoted, is bound to its enclosing function, and the only one allowed is in `insert_spawn_request`.
  SQL assembled at run time (`+`, `join`, a formatted table name) is outside that grammar (review of
  2662ceaa, N7).
- **Built from the definition only.** Runtime (from the harness), workspace, model, effort (as
  `runtimeConfig.effort`), instructions (`standing_instructions`), env, role and name. Nothing is carried
  from an earlier spec, and an empty model or effort is left empty for the harness to choose rather than
  filled from the service's managed defaults.
- **On the defining machine.** The environment is an online one whose `machineId` is the definition's
  (the session's own when it is one), never the fleet's freshest.
- **Mode.** A defined agent's mode is its definition's. Its `agents.session_mode` keeps saying resident
  until a managed worker registers, so the twin guard asks whether a resident session is live
  (`_has_live_worker_for`) rather than reading the effective column.
- **Refused before any start:** withdrawn (C6), resident, an invalid definition (`hostState: invalid`),
  and one its host reports it cannot run (`available: false`).
- **The launch** carries `definition: {storeId, incarnation, revision}`, or `null` for a start built
  without one, reached through the session's spawn request.

**Witnesses (P3/P4).** The first start of a newly defined id with no agent row and no session; a
definition changed between queue and launch (refused at the boundary); a withdrawal between queue and
launch (refused); managed to resident while running (worker untouched, then not offered); resident to
managed (offered for a managed start from then on).

**Settled while building P4** (2026-10-01, aify-env):

- **The boundary holds the definition until the process exists** (revised after review, N1).
  `startTerminal` hands the one call that makes the process to `admitStart(launch, produce)`, which the
  plugin answers with `DefinitionStore.admitStart`: it reads the file under the store's lock, refuses
  with the pure `startRefusal` (`lib/agent-definition-requests.mjs`), and otherwise runs `produce`
  (`processes.start`, including the Runner's checkpoint load) before letting go. A write therefore
  commits before the reading or after the child exists. The first version asked once and released the
  store before the process was made, and a set or removal in that gap started the old revision. A
  launch with `definition: null` is produced without the store. An adoption makes no process and is
  not checked. The refusals name the reason: another store, withdrawn
  on this host, invalid here, an earlier lifetime, a changed revision ("start it again"), or a harness
  whose runtime is not the launch's.
- **Applying a request is one lock hold** (`DefinitionStore.applyRequest`). The decision
  (`requestDecision`, C4 steps 1 to 4) and the write it leads to see the same state, so the write takes
  no second compare-and-set. A refusal is returned as the report, never thrown. An invalid file refuses
  even a removal: the request was made against the last good definition, not the hand edit since.
  A request's `null` sets the field's neutral value (`""`, `{}`, `true` for herdrSpace).
- **The definition sync is the plugin's third loop** (`definition-sync.mjs`). Every 10 s it claims,
  applies, reports, and only then publishes. It publishes when it applied something or 60 s after the
  last snapshot it published. The first pass publishes, and a failed push is tried again on the next
  pass. It runs while the plugin claims, and stops while the plugin is held or detached. An incomplete
  snapshot is never published.
- **A freed id is taken at once.** On `FREE_SINCE` the sync publishes one fresh revision
  (`snapshot({fresh: true})`), and only one per pass. `FREE_SINCE` and the harness-to-runtime table are
  spelled alike on both tiers, held by `service/tests/test_the_host_and_service_share_the_definition_vocabulary.py`.
- **The start list follows the host's own definitions.** For an agent this host defines, the mode is
  the definition's, the machine is this one by definition, and the status is still the roster's. It
  starts through `POST /agents/{id}/control start`. An `alreadyRunning` answer is a refusal, and a
  definition the service has not received yet is reported as not published. Every other agent keeps
  the session restart.
- **The daemon makes the store outside the statements its bootstrap tests evaluate**, so those tests
  hand the plugin a stand-in or nothing, never the operator's store.
- **The proof across both repos** is `service/tests/e2e/test_an_offline_request_reaches_the_hosts_file.py`:
  the real service as a process, and aify-env's store, client and sync run by node in a fresh process
  per visit of the host.

## C8. Plugins follow the registry without pretending the host stopped

Two operations, never one:

- **Host shutdown** (`plugin.stop()`, as today, `index.mjs:392-414`): offline beat with
  `heldTerminals: []`, because the host's workers go with it.
- **Configuration detach** (`plugin.detach()`, new): first the plugin enters QUIESCING, in which a
  claimed control that would start a process is refused on the control ("this service is being
  detached from this host"), and no new claim or control cycle begins; then it waits for the in-flight
  long-poll to return (at most its `waitMs`) and handles what it returned under the quiescing rule; then
  it re-evaluates custody. No offline beat, no empty `heldTerminals`.

**Policy when the registry changes**, decided AFTER the quiescing drain, on what the plugin holds then:

| change | plugin holds no workers | plugin holds workers |
|---|---|---|
| service added | start its plugin | n/a |
| service removed | detach completes | **kept**: the plugin stays attached serving its workers, back out of quiescing for everything except starts; `aify-env doctor` and the TUI show "registry change pending: N workers still held"; detach is retried when the last one ends |
| endpoint changed | the old plugin detaches, the new starts | **kept** on the old endpoint, the same report; applied when the last worker ends |

No worker is killed or declared gone by a configuration change. A daemon restart applies everything at
once, as today.

**Settled while building P2** (2026-10-01):

- **Held means the claim loop stays off.** "Everything except starts" is the control loop: input,
  resize and stop for the held workers. A claimed spawn is a start the plugin would have to refuse, so
  it claims none.
- **Only a registry that parses is followed.** `readServices` reads an unparseable file as no services,
  which is right for advertising and wrong for detaching: a typo would detach every plugin.
  `registryIsReadable` gates the follow.
- **A reverted change resumes.** If the registry names the held plugin's endpoint again, the plugin
  goes back to running and claims again; without this it would refuse starts until a daemon restart.
- **Where "registry change pending" shows:** the plugin logs it once on becoming held, and its state
  carries `phase` and `heldWorkers`. The doctor's `claiming` row fails on a held plugin, even though its
  claimer is accepted, and the TUI renders that row as the doctor wrote it.
- **A plugin authenticates as the entry it was built from.** Its key is resolved on every request
  from its own registry entry, never from whatever the registry names now, so a held plugin keeps the
  old endpoint's key binding, and a rotation of that key still reaches it (review of c1a4596, R1).
- **"A reply after detach"** is a heartbeat answer or failure arriving after the plugin detached;
  both are dropped. A control long-poll cannot arrive late: detach waits for the whole in-flight pass,
  including its setup, and a pass re-reads the phase after its setup.

**Witnesses (P2)** use the real aify-comms plugin with an injected fake transport and fake processes: a
held worker across a registry removal (still reported held, no offline beat); an endpoint change with a
claim in flight that returns a START control (refused, nothing started, then detach); the same with a
non-start control (handled, then detach); a delayed reply from the old endpoint after detach (ignored);
and daemon shutdown still sending the offline beat.

## C9. The launcher and its definition

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

**Settled while building P6** (2026-10-01, aify-wrapper), revised after its review (P6 R1-R4):

- **Owner ruling, 2026-10-01** (Steven, in the session that built P6): every agent has a model and an
  effort, on all three harnesses; agent info shows both; effort can be changed for any agent; there are
  defaults per harness; built in 0.8 with the rest of this tag. A role-only slice for codex and hermes is
  therefore not an option. The launcher half is below; the agent-info, change and per-harness-default
  half is C12.
- **One validator, copied.** aify-wrapper carries aify-env's `agent-definition-schema.mjs` byte for byte,
  held by a test that compares the bytes and runs the shared fixture through it. A launcher reads the
  one file through `bin/aify-definition.mjs`; it never asks aify-env's store, whose recovery writes.
- **Where each launcher applies model and effort**, each mechanism observed on the runtime, not read off
  its help:
  - claude: `--model` / `--effort` on its command line.
  - codex: `-c model=...` and `-c model_reasoning_effort=...` on the APP-SERVER's command line, never the
    TUI's. The TUI reads its configuration from the app-server and starts its thread with that model; a
    `-m` given to the TUI still beats it (codex-cli 0.159.3, `evidence/2026-10-01-p6/codex-model-probe.mjs`).
  - hermes: the model as `HERMES_INFERENCE_MODEL`, which seeds the session a hermes builds, the gateway
    host's included; the effort through aify-comms' delivery loop, which sets it on each live session
    with a session-scoped `config.set reasoning`, once per session. hermes has no launch-time effort lever
    on the gateway path (hermes 0.21.5, `evidence/2026-10-01-p6/hermes-model-probe.mjs`). A resumed hermes
    session keeps the model it was stored with, and a running gateway host keeps the seed it started with.
    The loop sets it on the session delivery targets, by delivery's own rule (`waitForActiveSession`):
    the session the agent's marker names by its live id, else the newest live one. The marker usually
    holds a durable key, which names no live id, so both go to the newest (review of P6r2, H3; this
    replaces "the marker's session, else the only one, else none", which set it nowhere there). One pass
    at a time; the loop starts it inside the part its finally covers, and teardown stops it before it
    awaits anything, so nothing new is sent after either (P6r2, H4). A plain `hermes chat` runs the
    classic CLI past the gateway, so it gets both as its own `-m` and `--reasoning`, placed after the
    subcommand wherever hermes' own `command_argv` finds it (P6r, L1; P6r2, L1).
  - codex refuses (78) a model or effort holding a control character: it takes both as TOML strings
    (review of P6r, L2). The definition reader names such a field (`AIFY_DEF_CONTROL`), because the
    shell drops a NUL and Git Bash a CR from the value it hands over; codex refuses one it selects, so an
    operator's or a managed launch's own value still wins over it (P6r2, L2).
  - Precedence is the same everywhere: the operator's own argument or runtime variable > a managed
    launch's `AIFY_MANAGED_MODEL` / `AIFY_MANAGED_EFFORT` > the definition > the runtime's own default.
- **A launcher that cannot run the reader** refuses with 78, as an invalid file does: not being able to
  look is not finding nothing. `--aify-ignore-definition` and a managed launch start without it.
- **One launcher per harness on every platform.** On native Windows each `.cmd` runs its bash launcher
  through Git Bash; hermes had a handwritten PowerShell launcher instead, which had none of this
  (review of P6r, H1), and it is gone. A native program started through the `.cmd` keeps its console
  (`evidence/2026-10-01-p6r2/cmd-to-bash-keeps-a-console.mjs`, in a pseudo-console). Every `.cmd` ends
  with its launcher's exit status, so a refusal is 78 there too (P6r2).
- **The definition is read for the resolved id.** An id a resume handle names is read after recovery and
  gets the same defaults and the same refusal as one given by a flag. `--check` resolves through the same
  function after the same argument loop, so it reports what the launch would use; it does not look up a
  resume handle, so such an id is not shown there.

## C10. Import shows what it does not know

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

**Settled while building P5** (2026-10-01, aify-env):

- **Import asks the running aify-env.** Each plugin holds its service's credential, so the command
  calls the daemon's `GET /agents/importable`, and the daemon asks every started plugin that offers
  `agents.importable` (`ServicePlugins.capabilities()`, plural; `capability()` still hands out one for
  starting). A service that fails is kept as a report carrying its problem. With no aify-env answering,
  the command says so and exits 65.
- **The service's vocabulary stays in its plugin.** `agent-import-records.mjs` maps aify-comms' roster
  row to definition fields: runtime to harness through the inverted `HARNESS_RUNTIME`, `cwd` to
  workspace, `sessionMode` to mode, and `runtimeConfig.effort`, then `thinking`, to effort (the order
  `launch_env.py` reads them). Only rows whose `machineId` is this host's, compared without case.
  `env` is always unreported and written `{}`. Any other field the row omits or leaves null is
  unreported too, written as its neutral value and named; an explicitly empty value is reported (review
  of P5, N5).
- **The plan is pure** (`lib/agent-import.mjs`). An id defined here is never planned for writing, and
  an unreadable file counts as defined. A service that could not describe an id leaves a note and gets
  no vote. Any field that differs is a conflict, listed with every service's value; `--prefer
  <service>:<id>` beats `--prefer <service>`, and neither applies to a service that did not report the
  id. Each importable row is checked as the store checks a new definition, plus an installed launcher.
- **`--write` fails closed.** If any service did not answer, nothing is written, since its conflict
  cannot show. Each import row is written with `expect: null`, so an id defined between the plan and
  the write is refused by the store, never overwritten.
- **The doctor has two rows.** `definitions` reads each plugin's own sync state from `/health`: it fails
  when a service predates definitions (`accepted: false`, set by a 404), when the last push or request
  failed, or when nothing has been published. `undefined-agents` names the agents the services know on
  this host that have no definition here, and PASSES, because they run as they did before.

**Settled with the P4 revision, for C4:** the service finishes every `done` removal whose consequence
is still owed in its reconcile pass (`service/reconcilers/owed_removals.py`), through the same fences as
the report route. The host claims only pending and claimed requests, so an owed removal never comes
back to it; the first version waited for a repeated report that no host would send.
A removal that fails is rolled back and stamped (`consequence_failed_at`), and the pass reads
never-failed removals first, so a failing prefix cannot starve the rest (second revision, N4).

**Settled with P5, for C11:** the sync logs each failure once per channel (requests, push) until it
changes or clears, so an old service's 404s are two log lines, not two every 10 s.

## C12. Model and effort for every agent

The owner ruling of 2026-10-01 (C9): every agent has a model and an effort, agent info shows both, effort
can be changed for any agent, and there are defaults per harness. What exists already, measured on
`next/env-owned-agents`: an agent record carries `model` and `runtimeConfig.effort`; the service has
per-harness defaults for NEW managed workers (`managed_claude_*`, `managed_codex_*`, `managed_pi_*`, none
for hermes), resolved by three hand copies (`spawn_requests.py`, `agents/environment_assignment.py`,
`routers/settings.py`); a defined agent's effort changes through a definition change request (C5).

- **Defaults per harness** stay where they are, the service's settings, which the dashboard already edits:
  one entry per harness DERIVED from the runtime adapters, hermes added, and one function that resolves a
  runtime's default, called by all three sites instead of each listing the runtimes. They fill a new
  agent's empty model or effort when the agent is created or assigned, so every agent then carries its own
  values. Alternative: defaults on the aify-env host beside the definitions, which would reach agents
  defined by aify-env's CLI; one move away if wanted.
- **Agent info** (`GET /agents`, `GET /agents/{id}`) carries `runsWith: {model, effort}`, each
  `{value, from}`: what the agent's NEXT start uses and who decides it. `from` is `definition` for a
  defined agent, `agent` for an undefined managed agent (its record, which its managed start reads), and
  `runtime` when nothing applies a value, with `value` "". A record's values are read the way its launch
  reads them (`service/api_core/model_effort.py`, one reader for the launch, `runsWith` and the
  defaults), and a damaged stored value reads as none. An undefined resident is always `runtime`: its
  launcher reads only a definition. What a runtime is actually running is NOT shown, because nothing
  observes it: no bridge reports a running model (`auto-registration.mjs` echoes the service's own value
  back), so a "reported model" field would be a label with no observation behind it.
- **Changing effort** is `PATCH /agents/{id}/effort {effort}`, one lowercase word or "" for the runtime's
  own, and the operator's for every agent (Steven, 2026-10-02: changing an agent's model and data is
  operator-protected): with an `OPERATOR_KEY` set, a caller that does not present it gets 403 and nothing
  is read or written. A defined agent's change is a definition change request its host applies (C4/C5), nothing written
  here; an undefined managed agent's is written to its record and to its spawn specs, under the write
  lock, with a stale `thinking` removed: a restart starts from its stored spec, and a start going running
  copies the spec's `runtimeConfig` over the record. A cleared effort is cleared in the launch as well:
  `AIFY_MANAGED_MODEL` and `AIFY_MANAGED_EFFORT` are in the launch's `unsetEnv`, so the host's own
  environment cannot supply one. An undefined resident's is refused (409) with the way to define it
  (`aify-env agents import`). Every accepted change says `appliesAt: "next start"`. Applying the defaults
  to existing agents clears the same stale values (review of P6r, C1-C4).
- **Split with the dashboard work:** the service routes and agent-info fields are this tag's; the
  dashboard controls are dashboard-manager's, agreed before either is built.

## C11. Mixed versions and persisted state

The service's definition rows outlive whoever pushed them, so each arm says what happens to them.

| arm | outcome |
|---|---|
| new service, no aify-env ever pushed | no definitions; everything behaves as today |
| new aify-env, old service | the push route 404s; the plugin logs it once, keeps its other loops; `aify-env doctor` reports "service does not accept definitions"; local list/show/validate work regardless |
| definitions accepted, then that machine's aify-env downgraded (or its new claimer never pushes) | the rows stay as last pushed and keep governing: descriptive fields, dispositions (C5) and withdrawal (C6) still apply; after 10 minutes with no push from the machine's current claimer, the dashboard and the service doctor show "definitions from <machine> not refreshed since <time>" |
| a start of a defined agent through an aify-env that does no C7 check | the service still builds the spawn from the definition it holds and still records the three binding fields; the old host ignores them. That is the one arm where the boundary check is absent, and the doctor row above names it |
| a withdrawn agent under an old aify-env | stays withdrawn; cold-starts refused as C6 until it is defined again or the operator releases it, after which it is an ordinary undefined agent |
| aify-dashboard | unchanged this tag |
