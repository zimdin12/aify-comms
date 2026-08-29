# The service adapter: how aify-env supervises something that is not a harness

**Status: DESIGN. Nothing here is built.** It is the first architecture slice of the operator's
2026-08-29 instruction — "get that aify-env and aify-comms bridge thing sorted out, real separation of
concerns" — and it is deliberately narrower than the harness-driver question, which follows it.

Companion to [AIFY_ENV_BOUNDARY.md](AIFY_ENV_BOUNDARY.md) (who owns what) and
[HARNESS_KNOWLEDGE_BELONGS_TO_AIFY_WRAPPER.md](HARNESS_KNOWLEDGE_BELONGS_TO_AIFY_WRAPPER.md) (where the
harness semantics go, later).

---

## What is actually wrong

The aify-comms bridge is a long-lived process on the same host as aify-env, doing a job aify-env is the
owner of: staying alive, being restarted, being replaced. Nothing supervises it. `install.sh` writes
the files and walks away, and the process keeps whatever it loaded at boot, indefinitely.

Measured on the operator's host on 2026-08-29, at the point this was written: the bridge was running
`579dd546`, started 25 August 04:53. Four days. Two user-visible defects sat fixed on disk that whole
time — the empty AGENT column in aify-env's process table, and transcript saving disabled for every
managed agent — and the only instrument that noticed was `aify-comms doctor`'s `bridge-current`, which
reports and cannot act.

The fix for both was one command. Nobody ran it because nothing said to.

**Staleness observability is not the separation.** It is an acceptance condition inside it. A better
restart mechanism under the same 31,823-line bridge has moved no concern. What moves a concern is
aify-env owning this process the way it owns every other one, and learning nothing new about agents to
do it.

## Why it is not a two-line allowlist change

aify-env recognises exactly one class of executable: a file carrying `HARNESS_WRAPPER_VERSION` with a
shebang, checked by `mayExecute()`. `server.js` has no such marker.

Marking it would be the wrong repair, and the reviewer was right to refuse it: the marker would then
be a lie told to pass an allowlist, and a generic execution policy would have been taught to confuse
two artifact classes. The adapter is not a harness wrapper. It needs its own kind.

That means a second artifact class, and a second class reaches further than it looks: the registry
schema and its three readers, authorisation, install receipts, process records, the health endpoint and
the TUI, restart policy, rollback, and Windows interpreter behaviour.

## The contract

### 1. The registry declares a DESIRED artifact

```json
{
  "hostAdapter": {
    "kind": "service-adapter",
    "releaseId": "<manifest-sha256>",
    "entrypoint": "<immutable-release-dir>/server.js",
    "manifest": "<immutable-release-dir>/manifest.json",
    "adapterAbi": 1
  }
}
```

**The identity is the MANIFEST digest, never the entrypoint's.** `server.js` imports a large tree; a
digest of it says nothing about the 174 other files that decide what it does. The manifest closes every
executable and runtime input in the release directory.

Declaration is desired state. It is **not** authorisation.

### 2. Producer and verifier share the arithmetic, never the population

This is the reviewer's correction to my first version, and it is the sharper rule:

- one canonical library owns path normalisation, manifest serialisation and digest calculation;
- the **installer** independently derives the candidate population from the release tree it just wrote,
  and emits the manifest;
- **aify-env** independently enumerates the immutable release directory, requires exact bidirectional
  closure against the manifest, hashes every listed byte, and refuses on any extra, missing or
  mismatched file before starting the entrypoint.

Two hand-written canonicalisers drift. One whole implementation producing and then accepting its own
population is worse: if the builder omits a runtime file, a verifier that calls the same builder omits
it too and blesses the same incomplete manifest. Shared arithmetic, independent enumeration.

### 3. Authorisation is a separate authoriser

Not `mayExecute()`, not a filename allowlist, not `HARNESS_WRAPPER_VERSION`. aify-env verifies artifact
kind, install receipt and manifest digest, then executes the recorded entrypoint. Unknown kind, unknown
ABI or a digest mismatch **refuses** and never falls back to launcher rules.

### 4. Lifecycle

1. install into a new content-addressed directory;
2. verify the complete manifest before publishing desired state;
3. atomic registry switch;
4. aify-env starts the replacement, reads its **loaded** identity, then retires the old owner — or uses
   a proven stop/start order where the port forbids overlap;
5. failed readiness preserves or rolls back to the prior desired release, explicitly;
6. the process row and the TUI carry desired release, loaded release, pid, start time and owner.

### 5. Three identities, not one

The desired adapter artifact, the loaded adapter artifact, and the loaded driver package and ABI. A pin
plus a lockfile is *source selection*, not runtime proof — as this repo proved to itself on 2026-08-29,
when the installed launcher and the consumed template disagreed while both said version `0.6.0`. The
adapter self-reports its driver version and content fingerprint; the service doctor compares that to the
consumed lock; aify-env only ever compares process-loaded to registry-desired.

## Acceptance

Contract tests, not predicate tests. This repo has shipped a feature that could never fire with six
green tests, because all six exercised the pure builder and none exercised the call site; and
`service-check.mjs` exists because a proven predicate sat behind an early return that never consulted
it. So:

- the refusal path is executed end to end — protocol, then authoriser, then runner — and the proof is
  that **spawn was never reached**, not that a predicate returned false;
- desired/loaded mismatch is driven through the real supervisor restart path;
- controls, each separately: remove one manifest row; add one unlisted file to the release directory;
  alter one byte; change the entrypoint; change the ABI; and mutate the invocation so that a verifier
  sitting behind a bypass goes RED rather than green;
- restart, rollback, single ownership, and no bare-`aify-comms` PATH collision are proven on a real
  host before the public alias is removed.

**"Delete the `aify-comms` command" means delete the public bare alias**, not the executable entry
point. The process still has to start, supervised. The 2026-08-11 incident was about the bare
invocation reaping a live fleet, and that is the thing that goes.

## Sequence, and what is NOT in this slice

1. this contract, designed and reviewed — where we are;
2. one vertical cutover: install receipt, registry declaration, aify-env authorisation and supervision,
   loaded-identity reporting;
3. remove the public alias, once 1 and 2 are proven on a real host;
4. **then** the harness-driver spike, on one narrow slice, before any harness code moves.

Not in this slice: moving any of the 15,013 harness-named lines, the driver ABI, the provider
conformance kit, or a harness broker. One adapter, one manifest format, one restart policy, no harness
extraction. If it grows past that, it has stopped being the first slice.
