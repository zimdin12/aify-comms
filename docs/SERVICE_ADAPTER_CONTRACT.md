# The service adapter: how aify-env supervises something that is not a harness

**Status: DESIGN, revision 6. Nothing here is built.** It is the first architecture slice of the
operator's 2026-08-29 instruction — "get that aify-env and aify-comms bridge thing sorted out, real
separation of concerns" — and it is deliberately narrower than the harness-driver question, which
follows it.

Revision 2 answered seven blockers raised in review of `4e139a34`: the artifact was self-referential,
the restart wording was unsafe for this particular process, and three named things (loaded identity,
the install receipt, registry concurrency) were words rather than contracts.

Revision 3 answered five blockers raised against revision 2. The pattern in all five is the same and worth
naming: **a thing this document requires by name but does not define is a thing the implementation gets
to invent.** The manifest had no schema while being release identity, entrypoint authority and ABI
authority. "Runtime dependency closure" had no population method, so Node builtins either violate it or
it means whatever a scanner happened to walk. Rollback had no binding to the transition it rolls back.
The singleton lease had no owner. The readiness stamps had no rule making them observations rather than
configuration.

Revision 4 cleared three contradictions revision 3 left standing, and they are the kind worth naming:
each was a place where the document said one thing in one section and its opposite in another, so an
implementer could satisfy it either way and neither reading would be wrong. A contract that can be
satisfied two ways has not decided anything.

Revision 5 fixed two things I introduced while fixing those. Both are the same mistake in different
clothes: **a property stated in one place and quietly assumed away in another.** Making the digest
algorithm configurable and then naming the release by a bare digest assumes one algorithm. Calling a
record immutable and then saying it becomes "spent" assumes it has a lifecycle. Neither survives being
read twice.

Revision 6 names the pattern behind all of them, because it has now happened three times in this one
document and the individual repairs were not teaching anybody anything:

> **A design may not promise a guarantee its storage cannot make.** "Every attempt gets an outcome"
> reads like a rule and is actually a claim about atomicity across two writes. "The old record is
> spent" reads like bookkeeping and is actually a lifecycle on something declared immutable. "The
> algorithm is declared inside the manifest" reads like flexibility and is actually an identity that
> cannot be compared. Each was fixed on its own; each came from the same habit of writing the property
> I wanted rather than the one the mechanism supplies.
>
> The test is mechanical: for every guarantee, name the write that establishes it and the failure that
> would break it. If the answer is "two writes and a crash between them", the guarantee is a wish and
> the honest version has an UNKNOWN state in it.

Companion to [AIFY_ENV_BOUNDARY.md](AIFY_ENV_BOUNDARY.md) (who owns what) and
[HARNESS_KNOWLEDGE_BELONGS_TO_AIFY_WRAPPER.md](HARNESS_KNOWLEDGE_BELONGS_TO_AIFY_WRAPPER.md) (where the
harness semantics go, later).

---

## What is actually wrong

The aify-comms bridge is a long-lived process on the same host as aify-env, doing a job aify-env owns:
staying alive, being restarted, being replaced. Nothing supervises it. `install.sh` writes the files
and walks away, and the process keeps whatever it loaded at boot, indefinitely.

Measured on the operator's host on 2026-08-29: the bridge was running `579dd546`, started 25 August
04:53. Four days. Two user-visible defects sat fixed on disk that whole time — the empty AGENT column
in aify-env's process table, and transcript saving disabled for every managed agent — and the only
instrument that noticed was `aify-comms doctor`'s `bridge-current`, which reports and cannot act.

The fix for both was one command. Nobody ran it because nothing said to.

**Staleness observability is not the separation.** It is an acceptance condition inside it. A better
restart mechanism under the same bridge has moved no concern. What moves a concern is aify-env owning
this process the way it owns every other one, and learning nothing new about agents to do it.

## Why it is not a two-line allowlist change

aify-env recognises exactly one class of executable: a file carrying `HARNESS_WRAPPER_VERSION` with a
shebang, checked by `mayExecute()`. `server.js` has no such marker.

Marking it would be the wrong repair: the marker would be a lie told to pass an allowlist, and a
generic execution policy would have been taught to confuse two artifact classes. The adapter is not a
harness wrapper. It needs its own kind, and a second kind reaches further than it looks — the registry
schema and its three readers, authorisation, install receipts, process records, health and TUI, restart
policy, rollback, and Windows interpreter behaviour.

## 1. The artifact, and where its boundary is

**BLOCKER 1.** Revision 1 put `manifest.json` inside the directory the verifier enumerates, and then
required exact bidirectional closure. A manifest cannot list its own digest, and a manifest that omits
itself is an unlisted extra the verifier must refuse. The layout has to state the payload boundary:

```text
releases/<manifestDigest>/
  manifest.json          envelope. NOT a payload member. Its digest names the release.
  payload/
    server.js            the entrypoint, as a relative manifest member
    ...                  every other governed runtime byte
```

- `<manifestDigest>` is the sha-256 of the **canonical manifest bytes** — one algorithm in v1, fixed by
  `manifestVersion` rather than chosen per artifact (1b says why).
- Every manifest entry is a path relative to `payload/`.
- aify-env enumerates `payload/` **exactly and only**, and requires closure in both directions: no
  listed file missing, no unlisted file present.
- `entrypoint` is a manifest member, not an arbitrary absolute path.
- Refused at verification: `..` segments, absolute paths, duplicate normalised paths, paths differing
  only by case (Windows resolves them to one file, so two entries can name one byte sequence), and
  symlinks, junctions and reparse points. What counts as a dependency that must be inside is bounded
  in 1c, because "any runtime dependency" is a rule no instrument can establish.

The member count is whatever the manifest says. Revision 1 quoted a file count for `mcp/stdio`; that
number is a measurement of a checkout, not the population authority, and it was already stale when it
was written.

## 1b. The manifest schema

**BLOCKER A.** The receipt had a field table and the manifest did not, while the manifest is release
identity, file population, entrypoint authority and ABI authority. Anything the registry or the receipt
says that the manifest does not authenticate can be substituted while the payload list stays identical.

| field | rule |
|---|---|
| `manifestVersion` | integer. An unknown version refuses; it never degrades to a best-effort read. |
| `service` | which service this adapter belongs to |
| `kind` | `service-adapter` |
| `adapterAbi` | the ABI the adapter implements. **Authenticated here**, so the registry and the receipt can only agree with it, never override it. |
| `entrypoint` | exactly one relative payload member. Not a list, not a pattern. |
| `digestAlgorithm` | **`sha256` in v1, and nothing else is legal.** Present so a future change is a declared change; not present so an implementation may choose. An unknown value refuses. |
| `files` | sorted rows of `{path, byteLength, digest, type}`, the digest computed with `digestAlgorithm` |
| `prerequisites` | declared externals (see 1c). Named, not hashed. |

- `type` is `file`. Directories, symlinks, junctions, sockets and devices are not members, and their
  presence under `payload/` is an unlisted extra, which refuses.
- **Canonical serialisation**, stated so two implementations produce the same bytes: UTF-8, LF, keys in
  lexicographic order at every level, no insignificant whitespace, integers only (no floats, no
  exponent forms), paths as slash-separated relative POSIX strings.
- Rows sorted byte-wise by canonical path, so ordering is not a degree of freedom.
- No duplicate paths, and no two paths equal after case folding: Windows resolves those to one file, so
  two rows could otherwise name one byte sequence.
- The release is named by the digest of the canonical manifest bytes. An unknown or mismatched
  algorithm **refuses** — it does not fall back to a default, because a default is how one side ends up
  hashing with something the other never agreed to.

**ONE ALGORITHM IN v1, AND THAT IS A DECISION.** Revision 4 made `digestAlgorithm` a field and left the
release identity a bare digest — in the registry, in the receipt, in the transition record and in the
directory name. A bare digest is an unambiguous identity only if exactly one algorithm is legal, so the
two halves contradicted each other: the field promised agility the identity could not carry.

The two coherent answers are a namespaced identity everywhere (`sha256-<digest>`) or a single algorithm
fixed by `manifestVersion`. **v1 takes the second.** Agility bought nothing here — there is one producer
and one verifier, both shipped together — and it would have put an algorithm selector inside an artifact
that is not yet trusted at the moment it is read, which is a strange place to take instruction from.
When a second algorithm is genuinely needed it arrives as `manifestVersion: 2`, and the migration is
that bump rather than a per-artifact negotiation.

If that decision is ever reversed, the identity must be namespaced in ALL FOUR places at once. A
namespaced digest in the manifest and a bare one in the directory name is the same contradiction moved.

**Where the shared library lives**, because "one canonical library" is otherwise an ownerless
component. It computes canonical serialisation and digests and nothing else: no filesystem walk, no
policy, no I/O. That makes it a leaf both sides can depend on without a cycle. aify-env depends on it,
the aify-comms installer depends on it, and it depends on neither. Which repo publishes it is an open
decision this slice must make before writing code, and the constraint is that it must not be published
by a repo that also consumes it through the other.

## 1c. What "inside the payload" can and cannot mean

**BLOCKER B.** Revision 2 said any runtime dependency resolving outside the payload root refuses. Taken
literally that is unimplementable and false: Node builtins, the Node executable, OS libraries and the
service's own mutable config all live outside by necessity, and a dynamic import or a runtime file read
evades any static walk. A rule no instrument can establish becomes whatever the scanner happened to do.

The bounded contract instead:

- **Manifest members**: every first-party JS file the adapter can load, and every byte of every npm and
  native package it can load. The population is derived from the PACKAGING mechanism that produced the
  release, not from a hopeful import walk.
- **Refused**: symlink, junction or reparse-point escape, and Node resolution reaching a parent or
  global `node_modules`.
- **Declared prerequisites, explicitly NOT artifact bytes**: the Node runtime identity and accepted
  version range, OS APIs and system libraries, and the configuration and data paths the service reads
  at runtime. Named in the manifest so a reader can see the boundary; contents not hashed.
- **A dynamic-import and runtime-file-read control** in acceptance, because that is the one gap a
  packaging-derived population cannot close by itself: an adapter reading a first-party file that is
  not a manifest member must fail visibly rather than work.

## 2. Producer and verifier share the arithmetic, never the population

- one canonical library owns path normalisation, manifest serialisation and digest calculation;
- the **installer** independently derives the candidate population from the release tree it just wrote,
  and emits the manifest;
- **aify-env** independently enumerates `payload/`, requires exact bidirectional closure, hashes every
  listed byte, and refuses on any extra, missing or mismatched file before starting anything.

Two hand-written canonicalisers drift. One whole implementation producing and then accepting its own
population is worse: if the builder omits a runtime file, a verifier that calls the same builder omits
it too and blesses the same incomplete manifest. Shared arithmetic, independent enumeration.

## 3. The registry declares a DESIRED artifact, and declaring is not authorising

```json
{
  "hostAdapter": {
    "kind": "service-adapter",
    "releaseId": "<manifestDigest>",
    "manifest": "releases/<manifestDigest>/manifest.json",
    "entrypoint": "server.js",
    "adapterAbi": 1,
    "receipt": "<path to the install receipt>"
  }
}
```

**BLOCKER 5 — the registry is shared, so an atomic rename is not enough.** `~/.aify/services.json`
carries entries several services write. A whole-file atomic replace still loses a concurrent writer's
update. Required:

- write through the existing canonical registry parser and writer, never a second one;
- preserve unknown keys and other services' entries **semantically** — every value and every unknown
  field survives, formatting need not, and 7b says why a canonical writer cannot promise more;
- compare-and-swap on an expected registry fingerprint or revision, with a bounded reread, merge and
  retry;
- refuse a malformed registry rather than rewriting it — aify-comms already owns only its own key for
  this reason, because overwriting would uninstall another service at the moment somebody reinstalls
  something unrelated.

Tests: two writers updating distinct services concurrently; a same-service write with a stale expected
revision refused; a malformed registry refused; and a rollback that preserves an unrelated concurrent
change. Whether `hostAdapter` is registry v1-compatible or needs a schema and reader rollout across
aify-wrapper, aify-env and aify-comms is an open question this slice must answer before it writes.

## 4. The install receipt

**BLOCKER 4.** Revision 1 named a receipt and defined nothing, so any implementation could invent one
and satisfy the prose. Schema:

| field | meaning |
|---|---|
| `receiptVersion` | schema version of this document |
| `service` | which service this adapter belongs to |
| `kind` | `service-adapter` |
| `releaseId` | the canonical manifest digest |
| `manifest` | canonical manifest location |
| `entrypoint` | relative payload member |
| `adapterAbi` | ABI the adapter implements |
| `installer` | installer identity and version that wrote it |

Stored beside the release, immutable once written, referenced by the registry entry rather than
searched for.

**THE RECEIPT DOES NOT CARRY A PREDECESSOR**, and revision 3's did. That was the third contradiction:
the receipt is immutable and lives beside a content-addressed release, but the same release can be
selected again later over a different predecessor — so a predecessor stored in it is right once and
wrong every time after. 7b then required a "newly bound transition record" that had no schema and no
custody. Two records, because they answer two questions:

| record | question | lifetime |
|---|---|---|
| **artifact receipt** | who installed THIS release, and what does it contain | one per release, immutable |
| **transition record** | this attempt to make that release desired | one per CAS attempt, immutable |

**AN IMMUTABLE RECORD CANNOT CARRY MUTABLE STATE OR BECOME "SPENT".** Revision 4 said the transition
record holds "the rollback state" and that a retry leaves the old one spent. Both give a lifecycle to
something declared immutable, and "the CAS that succeeded" has no referent for an attempt that failed.

So a transition is an APPEND-ONLY SEQUENCE of events, not a mutable row:

| event | written | carries |
|---|---|---|
| `attempted` | before every CAS | operation id, candidate release, expected-old predecessor, registry revision it was written against |
| `outcome` | after every CAS | operation id, and a typed result — `applied`, `conflict`, `refused` — with the new registry revision on `applied` only |
| `rollback_attempted` | before a rollback CAS | operation id, and the exact `applied` event it intends to reverse |
| `rollback_outcome` | after a rollback CAS | typed result, same three values |
| `rolled_back` | only after a `rollback_outcome` of `applied` | the reversal, as a fact rather than an intention |

**`rolled_back` CANNOT CARRY `conflict` OR `refused`**, and revision 5's did. Those outcomes mean no
rollback happened, so an event named for the thing having happened is a record of the opposite. The
attempt and its result are two events for the same reason the forward transition's are.

A retry appends. Nothing is overwritten.

A rollback names one `applied` event and succeeds only if current desired still equals that event's
candidate. Where a newer same-service desired value exists it **refuses** and the supervisor reconciles
forward, which is 7b's rule stated against a record that can actually be pointed at.

### The interrupted write, which revision 5 promised away

"Every attempt gets an outcome" is not a rule this design can keep. The CAS and the event append are
two writes, and a crash between them leaves an attempt with no result — so the honest contract has a
state for it rather than a promise it will not happen.

- **A durable OPERATION ID reaches the registry CAS itself**, not only the event log. Without it there
  is nothing in the registry to match a recovered attempt against.
- **After a restart, an attempt with no outcome is `OUTCOME_UNKNOWN`** until the registry's revision or
  recorded operation id proves the CAS applied or did not.
- **Candidate equality is NOT proof.** Current desired equalling the candidate does not mean THIS
  attempt applied it: another writer can select the same immutable release, which is the whole point
  of a content-addressed release being selectable more than once.
- **No further transition or rollback proceeds while an outcome is unresolved.** Reconciling forward
  past an attempt that may or may not have applied is how one supervisor undoes another's write.

Controls: kill between CAS and append and require `OUTCOME_UNKNOWN`; resolve it forward from the
registry revision; resolve it backward when the CAS did not apply; and a case where a second writer
selected the same release, where candidate equality would have resolved it wrongly.

**Said plainly: without a signature or an OS ACL, a local receipt is provenance under a local-host
trust boundary.** It records who installed what. It is not protection against an attacker who can
already rewrite the release store, and this document does not claim otherwise. If that threat model
becomes real, the answer is signing, not more fields.

## 5. Authorisation: an exact predicate, and both cross-kind refusals

**BLOCKER 7.** "Executes the recorded entrypoint" must mean this chain, each link checked:

1. the registry entry points to a receipt;
2. the receipt names a manifest digest;
3. the digest authenticates the canonical manifest bytes;
4. the manifest names exactly one entrypoint member;
5. the registry, the receipt and the AUTHENTICATED MANIFEST agree, three ways, on service, artifact
   kind, adapter ABI and entrypoint. Two-way agreement is not enough: registry and receipt can both be
   rewritten, and only the manifest is authenticated by a digest, so it is the tiebreak and any
   disagreement refuses;
5b. the manifest and receipt paths are DERIVED from the release and receipt roots rather than taken
   from the registry as given, so neither can carry a traversal to a file outside them;
6. aify-env executes only that normalised payload member, under the declared interpreter and argv
   contract;
7. an arbitrary `POST /processes` caller cannot select the service-adapter authoriser or substitute
   another path.

Unknown kind, unknown ABI or a digest mismatch **refuses** and never falls back to launcher rules.

**Controls in both directions**: an adapter offered through the launcher route is refused, and a
harness wrapper offered through the adapter route is refused. And a registered service must not be able
to ask aify-env to start another service's adapter.

## 6. Loaded identity: one handshake, and what it does not prove

**BLOCKER 3.** Revision 1 said aify-env "reads its loaded identity" and defined no carrier. One
consumed handshake:

- **Carrier**: a readiness frame the adapter writes on a dedicated channel opened by aify-env at spawn.
  Not stdout — stdout is the adapter's own output and a handshake sharing it is one an operator's log
  line can forge.
- **Timeout**: a bounded readiness window. Expiry is a failed start, not a slow success.
- **Fields**: service, artifact kind, adapter ABI, build and source stamp, pid, instance id, readiness
  state.
- **Binding**: aify-env binds the frame to the pid it spawned and to the manifest it verified
  immediately before spawning. A frame from a process aify-env did not spawn is refused.

**BLOCKER E: the stamps are OBSERVATIONS, never configuration.** Build, source and ABI fields in the
frame are owned by the build and may not come from a request, an environment variable or the registry.
`aify-comms doctor` was fixed this month for exactly that shape, where an environment-supplied build
value could manufacture equality with the sha it was being compared against, so the one stale-deploy
instrument agreed with a build nothing was ever made from. A supplied-stamp mutant is part of
acceptance. The dedicated channel proves WHICH child wrote the frame; it does not make the contents
true.

**Which pid is authoritative** is settled before implementation, not during it: a shell or interpreter
shim may be the process aify-env spawned while the frame comes from its Node child. The carrier choice
decides this and it is cross-platform (an inherited descriptor behaves differently on Windows than a
named pipe), so it is chosen together with its acceptance test rather than left to whichever works
first.

**The trust limit, stated:** a desired digest echoed back by the child is proof of transport, not proof
of loaded bytes. The adapter can only report what it believes it loaded. Either close the
read-then-spawn TOCTOU that `protocol.mjs` already documents, or state the immutable-directory and ACL
assumption explicitly and **re-verify the manifest after readiness** before accepting convergence.

Mutants, each separately: wrong pid; stale instance id; the desired digest echoed with a wrong build
stamp; no handshake at all; a handshake arriving after the timeout; and a valid handshake from a process
aify-env did not spawn.

## 7. Restart: one owner, always, and no blue/green

**BLOCKER 2.** Revision 1 offered "start the replacement, then retire the old owner — or stop/start
where the port forbids overlap", and port collision is not the deciding constraint. Two environment
bridges with the same machine and environment identity race claims, registration and survivor reaping
**on different ports**. That is the incident class this slice exists to remove — it is what happens
every time somebody runs a bare `aify-comms`, and it reaped nine managed agents on 2026-08-11.

So for adapter v1 there is exactly one restart policy, and concurrent ownership is forbidden:

1. freeze the desired transition (CAS);
2. ask the current owner to drain and stop;
3. prove the old pid, its process tree and its bridge lease are gone;
4. start exactly one new owner;
5. verify readiness and identity;
6. on failure, roll back under the binding in 7b;
7. prove one owner, after either success or rollback.

Blue/green can become a later ABI only if bridge identity and claim leases are redesigned first. A
contract that promises one restart policy must not carry two.

## 7b. Rollback is bound to the transition it rolls back

**BLOCKER C.** The receipt's `previousReleaseId` is observed BEFORE the registry CAS. A concurrent
same-service update can change the predecessor while this installer rereads and retries, and then
"atomically restore the previous release" overwrites a newer desired state somebody else just
published. The rollback would be atomic and wrong.

- the rollback names the `applied` EVENT it reverses, not the receipt, and not "the CAS that
  succeeded" — a retry appends a new attempt and outcome, and the earlier events stay exactly as they
  were written;
- the rollback CAS succeeds only if current desired still equals **this failed candidate**;
- if a newer same-service desired value exists, rollback **refuses**, and the supervisor reconciles
  forward to that newer value instead;
- unrelated services and unknown keys survive.

**Survive semantically, not byte for byte.** Revision 2 said "byte-semantically", which is not a thing
a canonical whole-JSON writer can promise: it cannot both canonicalise its own output and preserve
another writer's whitespace and key order. Every value and every unknown field must survive. Formatting
need not.

## 7c. The singleton lease has an owner

**BLOCKER D.** "Prove the bridge lease is gone" named an authority that does not exist. It belongs to
**aify-env**, on the supervisor route: an adapter self-reporting that it is the only one is precisely
the thing being guarded against.

- **Key**: `{service, artifactKind}`. One live adapter per service per environment.
- **Record**: lease id, adapter instance id, pid, process start time, and the release it was started
  from.
- **Acquisition** refuses while a live holder exists. Recovery requires the holder proven dead by pid
  AND process start time, because a recycled pid belonging to a foreign process is the failure a
  pid-only check invites, and this environment has already shipped a reaper that had to learn it.
- A record left by a dead aify-env instance is recovered by the next instance, which is the same
  ownership question `orphan-reap.mjs` already answers for processes.

Controls: two concurrent starts; a stale persisted lease with a dead pid; a recycled pid belonging to a
foreign process; an aify-env restart adopting its own record; and a rollback reacquiring exactly one
lease.

## 8. Two identities in this slice, not three

**BLOCKER 6.** Revision 1 required a loaded driver package and ABI identity while also excluding the
driver ABI from scope, and aify-wrapper has no runtime driver library today. That cannot be an
acceptance criterion for this cutover. For v1:

- the **desired** adapter manifest;
- the **executed** adapter instance, bound to the independently verified manifest plus its build and
  ABI handshake.

Every package and runtime dependency the adapter currently loads is a manifest payload member. Driver
package identity arrives with the driver slice, and no driver ABI is invented early to satisfy this
document.

## Acceptance

Contract tests, not predicate tests. This repo has shipped a feature that could never fire with six
green tests, because all six exercised the pure builder and none exercised the call site; and
`service-check.mjs` exists because a proven predicate sat behind an early return that never consulted
it. So:

- the refusal path is executed end to end — protocol, then authoriser, then runner — and the assertion
  is that **spawn was never reached**, not that a predicate returned false;
- desired/loaded mismatch is driven through the real supervisor restart path;
- the manifest controls, each separately: remove one row; add one unlisted file to `payload/`; alter one
  byte; change the entrypoint; change the ABI; and mutate the invocation so a verifier sitting behind a
  bypass goes RED rather than green;
- the handshake mutants from section 6;
- the registry concurrency tests from section 3;
- restart, rollback, single ownership and no bare-`aify-comms` PATH collision proven on a real host
  before the public alias is removed.

**"Delete the `aify-comms` command" means delete the public bare alias**, not the executable entry
point. The process still has to start, supervised.

## Sequence

1. this document, reviewed — where we are;
2. canonical manifest arithmetic, with the two independent population arms;
3. receipt, plus the registry CAS descriptor;
4. an adapter-only authoriser on a distinct supervisor route;
5. stop-old, prove-dead, start-new, with the handshake;
6. rollback and one-owner convergence;
7. desired and loaded readback in health and the TUI;
8. only after live proof, delete the public alias;
9. **then** the harness-driver spike, on one narrow slice, before any harness code moves.

Not in this slice: any of the harness-named lines, the driver ABI, the provider conformance kit, a
harness broker, test-stack tiering, or general launcher-allowlist repair. One adapter, one manifest
format, one restart policy. If it grows past that, it has stopped being the first slice.
