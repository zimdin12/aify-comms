# `mcp/stdio/server.js` — decomposition proof packet

**Status:** submitted for ruling. Measured at `37401488` on the 6,331-line `server.js`. **No extraction
performed.** The reviewer ruled server.js LAST and required a reviewed packet before any work; this is that
packet, written while the delivery-loop seam ruling is outstanding because measurement is read-only.

**Its conclusion is the opposite of what the file's size suggests, so it is stated first:** server.js is the
most tractable file left, not the least. A **144-line** layer-0 leaf unblocks **2,379 lines** across three
layers with **zero** residual mutable-state entanglement — taking server.js to ~3,952. The reason no earlier
pass found this is that nobody had measured it; 6,331 lines and "live MCP bridge" were treated as one fact.

**The ordering ruling is not challenged.** "server.js last" was justified on RISK — it is the live MCP bridge
every client loads — and tractability does not touch that. What follows changes what the work *is*, not when
it should happen. §8 is the risk section, and it is the one that should decide the timing.

---

## 1. Composition — where the 6,331 lines actually are

| region | lines | share |
|---|---|---|
| 112 top-level functions | 3,103 | 49% |
| 99 top-level `const`/`let`/`var` | 235 | 4% |
| 34 `server.tool(...)` registrations (L3801–L6276) | 2,047 | 32% |
| imports (89 lines), comments (561), blanks (300), remaining top-level statements | 946 | 15% |

The tool-registration region spans 2,476 lines end to end and had never been measured. It is 34 MCP tool
handlers, mean 60 lines, and it is subject-coherent in a way the rest of the file is not: each block is one
public MCP tool.

**Already decomposed:** server.js imports from ~30 sibling leaf modules. This is not an untouched monolith —
it is what remains after the easy leaves were already taken, which is why the remaining surface is entangled
rather than merely long.

---

## 2. Module-level mutable state — 35 names, and why that number misleads

server.js declares **35 module-level `let` bindings**. It is the bridge process, so process state lives at
module scope: `dispatchLoopTimer`, `spawnLoopBusy`, `environmentBridgeBootstrapped`, `__runtimeAdapter`,
`shutdownStarted`, four `__stop*Detector` handles, and so on.

35 mutable names in a 6,331-line file reads like the diffuse entanglement that stopped the hermes loop. It is
not, and the measurement that separates them is **direct readers per name**:

| | |
|---|---|
| functions whose closure touches ≥1 module `let` | 54 of 112 (2,301 lines) |
| functions whose closure touches none | 58 of 112 (802 lines) |
| **direct readers of any single `let`** | **1–4 functions** |

So the state contact is NARROW and the entanglement is TRANSITIVE. 54 functions are state-bound almost
entirely by inheritance through the call graph, not by touching state themselves. That is the property that
makes bottom-up work here: give a narrow state cluster its own owner and the inheritance stops.

The four `main`-cluster closures (`main`, `bootstrapEnvironmentBridge`, `ensureEnvironmentHeartbeat`,
`autoRegisterConfiguredAgent`) each reach 44–55 functions and 19–26 mutable names. That cluster — 52 functions
/ 1,662 lines, 54% of all function lines — is the process supervisor and is **not** a candidate for anything in
this packet. It stays.

---

## 3. The single blocker

Every one of the 34 tools was measured for mutable-state contact through its full transitive closure:

- **31 of 34 touch exactly ONE mutable name: `ACTIVE_SERVER_URL`** — and none of them touch it directly.
- 3 touch none at all (`comms_dashboard`, `comms_share`, `comms_read` — 284 lines).
- 1 outlier: `comms_register` (302 lines, 53-function closure, **25** mutable names).

`ACTIVE_SERVER_URL` is the multi-URL failover latch:

```
L168    let ACTIVE_SERVER_URL = SERVER_URLS[0] || "";      <- sole declaration
L1327   ACTIVE_SERVER_URL = baseUrl;                        <- SOLE WRITER, inside httpCall
```

**One writer.** Four direct readers: `httpCall` (58L), and three failure-logging helpers
(`logTransientOrError` 8L, `noteControlClaimFailure` 18L, `noteSpawnClaimFailure` 22L).

A 2,000-line region blocked by one variable with one writer is a different problem from a 619-line loop whose
seams each capture their own `let`.

---

## 4. Why the state here CAN have an owner and the loop's could not

This is the load-bearing distinction, and it is the reason this packet recommends work where the seam packet
recommended stopping.

| | delivery loop | server.js |
|---|---|---|
| where the state lives | function scope, inside `runDeliveryLoop` | **module scope** |
| who can own it | only the loop — a module cannot hold a function's local | **a module**, since ESM module state is already a per-process singleton |
| what a mover needs | a factory/signature change per seam | an **import** |
| preserves identity? | no — a captured `let` becomes a parameter | yes — same single latch, same process |

Moving a module-level `let` into a module that owns it is not a restructure. It relocates a process-global to
a narrower owner while keeping exactly one instance of it. Moving a captured `let` out of a closure changes the
signature of everything that reads it. Same word, "mutable state"; different problem.

---

## 5. The proposed sequence — three layers, measured

### Layer 0 — the latch gets an owner (144 lines, 7 functions)

`uniqueServerUrls` (10L) · `isRetriableRequest` (17L) · `isTransientHttpError` (11L) ·
`logTransientOrError` (8L) · `noteControlClaimFailure` (18L) · `httpCall` (58L) ·
`noteSpawnClaimFailure` (22L)

Owns `ACTIVE_SERVER_URL`. Reads 11 module constants, which follow it or stay by the sole-reader rule:
`SERVER_URL`, `SERVER_URLS`, `HTTP_TIMEOUT_MS`, `HTTP_RETRY_ATTEMPTS`, `HTTP_RETRY_BASE_MS`,
`RETRIABLE_POST_PATHS`, `CONTROL_CLAIM_FAILURES`, `API_KEY`.

**`API_KEY` is env-derived and stays env-derived.** No key material moves into any new file; nothing in this
packet reads, logs or relocates a value. Naming the binding is the whole of what is reported here.

**One question I am NOT deciding: CLOSURE ≠ OWNER.** `noteControlClaimFailure` and `noteSpawnClaimFailure`
are in the closure because they READ the latch to log which server failed — but they are claim-failure
bookkeeping, not HTTP, and `noteSpawnClaimFailure` drags two more mutable names (`spawnClaimFailureCount`,
`spawnClaimLastLogAt`) that are spawn-loop state. Two options, and the reviewer should pick:

- **(A) membership** — they join the leaf, which then owns three mutable names, one of which is about spawning.
  Simplest, byte-identical, and gives the leaf a second subject it should not have.
- **(B) accessor** — the leaf exports `activeServerUrl()`; the two note-helpers stay in server.js and call it.
  Keeps the leaf single-subject; costs a body edit in each (`ACTIVE_SERVER_URL` → `activeServerUrl()`), so
  those two are no longer byte-identical relocations and need their assertion stated as substitution.

I lean **(B)**: the leaf's subject is "how to reach the service, with failover", and spawn-claim counters are
not that. But (B) breaks byte-identity for 40 lines, which is exactly the kind of trade the reviewer has ruled
on differently than I expected before.

### Layer 1 — the helper layer (490 lines, 29 functions)

The 33 tools (excluding `comms_register`) transitively need 29 local helper functions. **All 29 become
state-free the moment layer 0 owns the latch — zero remain state-bound.** That is not an estimate; the
residual set is empty.

### Layer 2 — the tool region (1,745 lines, 33 tools)

Only reachable after layers 0 and 1. **The measurement that proves the order matters:**

| moved as | names the new module must import |
|---|---|
| all 33 tools, today | **65** (33 local fns, 24 local consts, 8 re-imports) |
| channel tools alone (5 tools, 245L) | 32 |
| message tools alone (6 tools, 490L) | 42 |

A 245-line module needing 32 imported names is not a decomposition — it is server.js with a second address,
and it would force ~57 names to become `export`s, turning the bridge entry point into a library of its
internals. **This is the negative result that kills the obvious approach of starting at the tools.** Layers 0
and 1 are what shrink that surface; the tool region must go last.

### Total

**144 + 490 + 1,745 = 2,379 lines**, server.js **6,331 → ~3,952**. `comms_register` (302L) and the
`main` supervisor cluster (1,662L) stay, correctly.

---

## 6. The one thing in layer 2 that is NOT a byte-identical move

`server.tool(...)` is a call on the `server` object. A tool module cannot execute it at module scope, so each
group needs a registration wrapper:

```js
export function registerChannelTools(server, deps) {
  server.tool(  /* handler body, byte-identical */  );
}
```

The **wrapper is new code**; the **handler bodies are byte-identical**. Whether that counts as a v0.5.x
structural move is the reviewer's call, and it is the same question the hermes factory conversion raised with
one difference worth weighing: here the new code is a thin registration shell around unchanged bodies, whereas
factory conversion rewrites how each seam obtains its state. I think this side of the line is defensible and I
am not assuming it.

---

## 7. Duplication finding — reported as maintenance, NOT as a defect

Four HTTP callers exist under `mcp/stdio`: `server.js`, `claude-channel.js`, `hermes-channel.js`, and
`aify-http.mjs` (the neutral owner created in v0.5.4). `claude-channel.js` carries its **own**
`let ACTIVE_SERVER_URL` (L62), its own `uniqueServerUrls`, and the same latch-on-success at L174.

**I checked whether two latches can coexist in one process before calling it a bug, and they cannot in the
path that matters.** `server.js` is the MCP bridge entry point (`bin.aify-comms-mcp`) and does not import
`claude-channel.js`; claude-channel is a spawned sidecar. Separate processes, so separate failover latches are
correct, not inconsistent. `hermes-channel.js` does import `claude-channel.js`, but only for `dispatchContent`,
leaving that copy of the latch merely unused.

So this is code duplication with a maintenance cost — a failover fix must be made twice — and **not** a
correctness defect. It is the same whole-class shape as `sleep` being defined five times, and it should be its
own cleanup with its own ruling, not smuggled into a decomposition slice. Recorded here so the next reader does
not have to re-derive it, and stated this way because "two independent latches" is the kind of finding that
reads as a bug until someone checks the process boundary.

---

## 8. Risk — the section that should decide the timing

- **server.js is the live MCP bridge.** Every Claude Code / Codex client loads it at startup. A syntax or
  export error here is not a failing test, it is every agent losing its tools.
- **`httpCall` is the hottest path in the file.** Layer 0 moves the function through which every one of the
  31 state-touching tools reaches the service.
- **Host code: inert until `install.sh` is re-run AND every wrapper relaunches.** The suites prove repo code,
  never live bridge state. `install.sh` must be run sequentially, never in parallel.
- **`bridge-current` will read red** for any live bridge still running the pre-slice build until wrappers
  relaunch. That red is accurate and must not be "calmed".
- **Two module instances would mean two latches.** The one way this becomes a real defect is if the new leaf
  is ever reached through two different specifiers in one process. Whatever is extracted, that is the property
  to assert, and I would want it asserted rather than reasoned about.

---

## 9. What would prove it

The `extraction-proof.mjs` pattern already used for app.js and hermes: ONE pristine tracked fixture of
pre-slice `server.js`, plus a plan each slice appends to, reconstructing byte-identically. That prover already
handles functions, `const`/`let`/`var` spans, multi-line import blocks and `pristineExported`, so it needs no
new capability for layers 0 and 1. Layer 2 needs one addition — reconstructing an indented `server.tool(...)`
block, which is a paren-balanced span at non-zero indentation rather than a declaration at column 0.

Plus real unit tests that CALL the extracted code (the `doctor-predicates.js` standard). For layer 0 the
interesting assertions are the ones the current tests cannot make, because `httpCall` is unexported and
unreachable: that the latch advances only on success, that it is NOT advanced by a retriable failure, and that
a timeout is distinguished from an HTTP error.

---

## 10. Asking

1. Does layer 0 (144 lines, the latch gets an owner) go ahead, and when — now, or after the operator's scope
   decision on the remaining files?
2. **(A) membership or (B) accessor** for the two claim-failure note helpers? I lean (B); it costs byte-identity
   on 40 lines.
3. Is the layer-2 `registerXTools(server, deps)` wrapper a v0.5.x structural move, given the handler bodies are
   byte-identical?
4. Does the four-way HTTP-caller duplication become its own packet alongside the `sleep`-×5 cleanup?

---

## CORRECTIONS AFTER EXECUTION (`97192fe8` onward)

This packet was measured before any extraction. Doing the work falsified two of its numbers. Both are
recorded here rather than quietly fixed, because the packet is the artifact the operator and reviewer
decided from.

### 1. Layer 0 was not 144 lines / 7 functions

It is **15 items / ~190 lines**: five functions, eight constants, plus `splitServerUrls` and
`defaultFallbackServerUrls` pulled in as transitive closure.

The packet scoped layer 0 as "the latch and its readers". It missed that `SERVER_URLS` is BUILT with
`uniqueServerUrls` at module scope while `httpCall` READS `SERVER_URLS` — so leaving the constants behind
would have made the leaf import upward from the bridge, a cycle. URL resolution belongs with the latch,
and the honest subject is not "the failover latch" but "how to reach the aify service at all".

Delivered in `97192fe8`. server.js 6330 → 6203.

### 2. Layer 1's residual set is NOT empty — this is the bigger error

The packet said: *"All 29 become state-free the moment layer 0 owns the latch — zero remain
state-bound. That is not an estimate; the residual set is empty."* Measured after layer 0 actually
landed:

| | packet | measured |
|---|---|---|
| helpers the tool region needs | 29 / 490 lines | **70 / 1,880 lines** |
| state-free after layer 0 | 29 / 490 | **55 / 992** |
| still state-bound | **0** | **15 / 888** |

Fifteen helpers still touch module state, headed by `runDispatchLoop` (449 lines, dragging the four
`__stop*Detector` handles), `runManagedTeardownForBridge`, `ensureRequiredReplyHandoff` and
`armClaudeTurnEndDetector`.

**Why the original number was wrong:** it counted the helper closure of the 33 tools excluding
`comms_register` and stopped at direct helpers, rather than taking the full transitive closure. The
closure reaches the dispatch loop and the teardown machinery, which are bridge lifecycle rather than tool
support.

**What this changes.** Layer 1 is still worth doing — 55 functions and 992 lines are genuinely
state-free and extractable — but it is not the clean single lift the packet described, and the tool
region (layer 2) still cannot follow it directly while 888 lines of its closure remain state-bound in the
bridge. `runDispatchLoop` in particular is the same category as the hermes delivery loop: lifecycle state
captured across a long-running function, not a relocatable helper.

**The lesson, stated because it is the second time in this series:** a closure measured before the
prerequisite lands is a prediction, not a measurement. Layer 0's own definition changed once the move was
attempted, and layer 1's changed once layer 0 existed. Re-measure at the boundary, every time.
