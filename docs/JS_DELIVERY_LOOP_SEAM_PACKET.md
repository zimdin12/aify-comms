# `runDeliveryLoop` — seam decomposition packet

**Status:** submitted for ruling. No extraction performed. Measured on `dba70935`.

The reviewer's ruling: do not move `runDeliveryLoop`'s 619 lines as one body; write a seam packet with seven
specific measurements and get a ruling before anything moves. This is that packet.

**Its conclusion is not the one I expected, so it is stated first:** exactly ONE seam inside this loop can be
moved byte-identically, and it is 4 lines. Every other candidate is a closure over mutable loop state, and
extracting those requires converting them to factories — a signature change, which is not a v0.5.x structural
move. Detail in §5 and §8.

---

## 1. Mutable-state ledger

`runDeliveryLoop` is 619 lines. Its first 55 are a single destructured parameter list — the injected
dependencies. Inside it declares **32 function-scope bindings**: 11 `let`, 4 mutable objects, and 8 closures.

### Mutable bindings

| binding | kind | reassignments | read by | lifetime |
|---|---|---|---|---|
| `gatewayChild` | `let` | 2 | `teardown`, `installTeardown` getter | whole loop |
| `host` | `let` | 2 | `ensureWs`, `effClearMarkers`, `reportGatewayDeadOnce`, tick body | whole loop |
| `wsClient` | `let` | 3 (+2 method calls) | `ensureWs`, `readManagedSessionStatus`, `countAttachedSessions`, tick catch | whole loop |
| `claimerLeaseAcquired` | `let` | 1 | `postClaimerLease` | whole loop |
| `gatewayDeadReported` | `let` | 2 | `reportGatewayDeadOnce` | whole loop |
| `reEnsureBudget` | `let` | 2 | tick body, re-ensure decision | whole loop |
| `statusRpcId` | `let` | incremented | `readManagedSessionStatus` | whole loop |
| `attachRpcId` | `let` | incremented | `countAttachedSessions` | whole loop |
| `noTuiCycles` | `let` | 1 | no-TUI detection | whole loop |
| `hasSeenAttachedTui` | `let` | 1 | no-TUI detection | whole loop |
| `totalProcessed` | `let` | accumulated | every exit's return value | whole loop |

### Mutable objects (identity matters — moving one changes what a closure observes)

| object | shape | mutated by | read by |
|---|---|---|---|
| `teardownState` | `{ done: false }` | `makeTeardown` via `teardown` | teardown idempotence |
| `inFlight` | `{ submittedAt, completed, runId, observedWorking, … }` | 3 property writes | repulse, turn detector, delivery |
| `claimErrorCounter` | `{ count: 0 }` | claim error path | claim backoff |
| `emptyAttachCounter` | `Map` | attach detection | no-TUI decision |

**Note this loop declares its own `teardownState`** — a second object of the same shape as the module-level
`_teardownState` that moved to `hermes-gateway.mjs` with `installShutdownTeardown`. They are distinct: the
gateway one guards process-shutdown teardown, this one guards per-loop teardown. Worth stating because a
future reader seeing two `{ done: false }` objects will reasonably suspect duplication.

---

## 2. Exit / teardown matrix

The loop body is wrapped in `try { … } finally { stopLiveness(); stopRepulse();
stopGatewayTurnDetector(); stopGatewayProbe(); }`. So **every exit runs the four stop handles**.

| # | exit | offset | inline stops before it | teardown | return |
|---|---|---|---|---|---|
| 1 | empty agent id | +64 | none (declared before `stopLiveness` exists) | no | `{released:false, processed:0}` |
| 2 | gateway never came up | +174–175 | `stopLiveness()` | no | `{released:false, processed:0}` |
| 3 | resident-lost (no-TUI / gateway dead) | +535–540 | all four | yes | `{…, residentLost:true}` |
| 4 | terminal (agent removed/stopped) | +570–574, +594–595 | all four, then `procExit(0)` | yes | `{…, terminal:…}` |
| 5 | released | +598–599 | none inline | yes | `{released:true, …}` |
| 6 | max iterations / loop end | +612 | none inline | no | `{released:false, …}` |
| 7 | tick error | +601–609 | none — closes `wsClient`, nulls it, continues | no | (no exit; loops) |

**FINDING, offered as an observation rather than a defect:** exits 3 and 4 call all four stop handles inline
AND then the `finally` calls them again. So on those paths every stop handle runs **twice**. That is harmless
if each is idempotent and I have not proved that it is — `stopLiveness` and friends come from four different
modules. It matters for a seam because any seam that moves one of those blocks must preserve the double call,
and a "tidy-up" that removed the inline stops would change behaviour on the paths where a stop is not
idempotent. I am not proposing to change it.

Exit 1 returns before `stopLiveness` is even declared, so its lack of stops is correct, not an omission.

---

## 3. Ordering invariants

Measured from the source, in execution order:

1. `resolveGatewayPort` (+66) → `startLivenessHeartbeat` (+72) → `startResumeMarkerSync` (+87). **Liveness
   heartbeat starts BEFORE the gateway host is ensured** (+153), so an agent whose gateway never comes up is
   still visible as alive-but-degraded rather than silently absent.
2. `installTeardown` (+143) is registered BEFORE the first gateway spawn (+153), so a process death between
   spawn and readiness still tears the child down.
3. `gatewayChild = host.child` (+177) only after the ensure retry loop succeeds; `teardown` reads it through
   a getter, so it sees the assignment whenever it happens.
4. `stopGatewayProbe` (+229) is created after `reportGatewayDeadOnce` (+210) because the probe calls it.
5. `stopRepulse` (+339) and `stopGatewayTurnDetector` (+372) are created after `inFlight` (+263) — both
   observe that object, and both are stopped in the `finally`.
6. Counters (`totalProcessed`, `claimErrorCounter`, `emptyAttachCounter`) are declared immediately before the
   `try` (+412–420) and are the only state the `finally` does not touch.
7. Inside the tick: claim result is handled before `sleepImpl(POLL_MS)` (+610), so a released or terminal
   result exits without a further poll delay.
8. Marker clearing (`clearSessionMarker`, +589) happens **only** on the terminal path, not on release —
   deliberate, and the surrounding comment says why: the next launch must resume the same transcript.

---

## 4. Call classification

| class | calls |
|---|---|
| already-extracted owners | `hermes-gateway.mjs`: `ensureGatewayHost`, `openGatewayWsClient`, `gatewayUnreachableMessage`, `maybeReEnsureGatewayHost`, `isGatewayConnectRefused`, `sleep` · `hermes-active-session.mjs`: `waitForActiveSession`, `activeListRowsLocal`, `sessionKeyFor`, `startResumeMarkerSync` · `hermes-run-reporting.mjs`: `reportTurnBusy`, `clearTurn`, `markRunDelivered`, `markRunFailed`, `markRunRequeued` · `hermes-inflight.mjs`: `makeInFlightProbe`, `makeInFlightPulse` · `aify-http.mjs`: `makeAifyHttpCall` · `hermes-env.mjs`: `TMP_DIR`, `RUNTIME` |
| existing sibling leaves | `hermes-endpoint.js`, `hermes-gateway-protocol.js`, `hermes-turn-repulse.js`, `hermes-gateway-turn-detector.js`, `hermes-loop-ready.js`, `liveness-heartbeat.js`, `hermes-daemon.js` |
| loop-owned closures | the 8 in §5 |
| injected, must stay injected | the 55-line destructured parameter list — `spawnImpl`, `sleepImpl`, `httpCall`, `procExit`, `installTeardown`, `startLivenessHeartbeatImpl`, `clearMarkers`, `maxIterations` and the rest. These exist so tests can drive the loop without a gateway; a seam must not capture them. |
| host-only, must NOT ride along | `runCli` (37), `runEnsureHostCli` (48) — argv parsing and CLI entry. **`runCli` calls `runDeliveryLoop`**, so CLI and loop are one connected component; the boundary is that the CLI owns argv and process lifetime, the loop owns delivery. |

---

## 5. Candidate seams — and why there is only one

Every closure declared inside the loop, with the mutable state it captures:

| closure | lines | captures |
|---|---|---|
| `wsIsOpen` | 4 | **NONE** |
| `teardown` | 6 | `gatewayChild`, `teardownState` |
| `reportGatewayDeadOnce` | 11 | `gatewayDeadReported`, `host` |
| `countAttachedSessions` | 11 | `attachRpcId`, `wsClient` |
| `postClaimerLease` | 12 | `claimerLeaseAcquired` |
| `ensureWs` | 13 | `host`, `wsClient` |
| `effClearMarkers` | 15 | `host` |
| `readManagedSessionStatus` | 31 | `statusRpcId`, `wsClient` |

**Seven of eight capture mutable loop state.** A closure that captures a `let` cannot be relocated
byte-identically: the moved copy would reference a binding that does not exist in the new module. The only
ways to move one are

- **pass the state in** — a signature change, so not byte-identical, and every call site changes; or
- **convert it to a factory** — `makeManagedSessionStatusReader({ ensureWs, sessionKey })` returning the
  closure. This is the pattern the codebase already uses (`makeInFlightProbe`, `makeTeardown`,
  `makeGatewayReachabilityProbe`, `makeInFlightPulse` are all factories), so it is idiomatic here — but it is
  still a restructure, not a relocation.

**The one byte-identical seam is `wsIsOpen`, 4 lines**, a pure predicate over its argument. Extracting 4 lines
does not reduce a 619-line function in any meaningful sense.

---

## 6. Negative proof

For the only movable seam (`wsIsOpen`):

- does NOT drag `deliverRun` — it calls nothing;
- does NOT own API client construction — `aify-http.mjs` owns that as of `fe6f1790`;
- does NOT import upward from `hermes-managed-host.js` — it has no dependencies at all;
- no cycle possible.

For the factory-conversion option, the same three proofs are achievable per seam but only **after** the
conversion, because pre-conversion the closures cannot leave at all.

---

## 7. Runtime-risk evidence

If any seam moves, focused tests must execute every exit branch it touches. The existing
`hermes-managed-host.test.js` already drives the loop through injected dependencies (`maxIterations`,
`sleepImpl`, `httpCall`, `procExit`), which is what makes the branches reachable without a gateway. A seam
touching the teardown blocks needs exits 3, 4 and 5 exercised; a seam touching ws lifecycle needs exit 7 (tick
error) as well, since that path closes and nulls `wsClient`.

Deployment language unchanged: repo code only, live bridge unchanged until `install.sh` and wrapper relaunch,
no wrapper relaunch while managed agents are live.

---

## 8. What I recommend, and the ruling I am asking for

**Byte-identical relocation cannot decompose this loop.** That is a measurement, not a preference: 7 of 8
internal seams are closures over 11 `let` bindings and 4 mutable objects, and the loop's remaining bulk is
the tick body that reads all of them.

Three options:

- **(a) Stop here.** `hermes-managed-host.js` is 1,879 lines, down from 3,017. The delivery loop stays whole
  and the file stays over 1,000. Honest, and leaves the operator's target unmet for this file.
- **(b) Factory conversion as its own tag, outside v0.5.x.** Convert the loop's closures to factories in the
  style the codebase already uses, with characterization tests written first, then relocate them as ordinary
  slices. Same behaviour, different structure — which is exactly the category v0.5.x excludes, and exactly
  what the Python route-handler method-split question is also blocked on.
- **(c) Move the loop whole.** Byte-identical, provable, and a 619-line span nobody can review by eye. The
  reviewer has already ruled against this and I agree with the ruling.

**My recommendation is (a) now and (b) proposed as a separate tag**, with the reviewability point stated
plainly: for this function, byte-identity would be a strong proof of a weak claim. The hash would tell you
nothing moved; it would not tell you the module boundary is sensible, and a 619-line body in a new file is not
a decomposition.

This is the same shape as the `app.js` ceiling: the remaining reduction needs a behavioural-shaped change, and
that is the operator's call rather than mine.
