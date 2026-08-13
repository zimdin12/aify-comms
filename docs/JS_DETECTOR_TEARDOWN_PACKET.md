# Detector state and teardown — seam packet

**Status:** submitted for ruling. Measured at `7a18ac5a`. **No extraction performed.**

Written to satisfy the reviewer's step-2 condition: *"detector-state packet only after measuring
callers/readers and proving whether private operations cover them. Do not assume controller-activity shape
until the reads are counted."* The reads are counted, and the measurement changes the grouping the sequence
assumed.

---

## 1. CORRECTION: `__runtimeAdapter` is not detector state

The accepted sequence named "the four `__claude*` detector names". One of those four is not detector state.
`__runtimeAdapter` has **16 references across six distinct concerns**:

| lines | concern |
|---|---|
| 261–264 | its own resolution from `AIFY_RUNTIME` |
| 270 | the session-handle heartbeat's config |
| 370–372, 391 | the **claude** turn-end detector |
| 450–459 | the **codex** turn detector — a different detector entirely |
| 679–682 | diagnostics (`getCurrentSessionId`, `diagnosticEnv`) |
| 1336 | `computeInitialSessionHandle` |
| 3439 | `fillSessionHandleFromAdapter` |

It is the bridge's runtime adapter: a process fact, resolved once at load, read by six unrelated things. That
is the shape `IS_REMOTE` and `AIFY_AGENT_ID` had, and mistaking it for group state is the same error I made
when I recorded those two as dispatch-group dependencies. **A name the detector reads is not a name the
detector owns** — third time this rule has fired in the lane.

It needs its own owner, and that owner is a prerequisite for anything else here, because the claude arming
function reads it at 370–372 and 391.

## 2. The claude detector's own state — three names, and operations DO cover them

| name | refs | where |
|---|---|---|
| `__effectiveAgentId` | 7 | **all seven inside L362–408** — the arming block and its callbacks |
| `__claudeTurnDetectorArmed` | 4 | decl 363, read 368, write 377, **one external read at 3565** |
| `__stopClaudeTurnEndDetector` | 3 | decl 364, write 378, **one external call at 737** (`cleanupOnExit`) |

So the external surface is exactly three operations — `armClaudeTurnEndDetector()`,
`stopClaudeTurnEndDetector()`, `isClaudeTurnDetectorArmed()` — and **no raw handle needs to leave the
module**, which is the condition the reviewer set. The controller-activity shape does transfer, verified
rather than assumed.

`__effectiveAgentId` is worth naming as a concept rather than a variable: it initialises from
`AIFY_AGENT_ID` and is **reassigned at L376** when the detector arms. It is "the id this bridge is currently
acting as"; `AIFY_AGENT_ID` is "the id it was launched as". `launch-identity.mjs` deliberately owns only the
second, and the source comment explains why the first must be a variable — the detector can be armed late,
from the register handler, once the effective id is known.

## 3. THE LARGER FINDING: this is one of SEVEN identical handles

Measuring the claude detector surfaced the pattern it belongs to. There are seven stop handles in
`server.js`, and every one is the same idiom: declared, assigned once, called exactly once in
`cleanupOnExit`. Nothing else reads any of them.

| line | handle | form | started |
|---|---|---|---|
| 269 | `__stopHandleHeartbeat` | `const` | unconditionally |
| 306 | `__stopTurnBusyHeartbeat` | `const` | unconditionally |
| 323 | `__stopLivenessHeartbeat` | `const` | unconditionally |
| 364 → 378 | `__stopClaudeTurnEndDetector` | `let = () => {}` | only when armed (late, per-agent) |
| 447 → 454 | `__stopCodexTurnDetector` | `let = () => {}` | only when the runtime is codex |
| 585 → 587 | `__stopGatewayProbe` | `let = () => {}` | only when a gateway exists |
| 627 → 640 | `__stopResidentHermesTurnDetector` | `let = () => {}` | only for resident hermes |

**The `const`/`let` split maps exactly onto unconditional/conditional.** The four `let`s default to
`() => {}` for one reason: so `cleanupOnExit` can call them unconditionally without checking whether they
were ever started. And `cleanupOnExit` carries seven consecutive `try { … } catch { /* best effort */ }`
lines to match.

## 4. Two options, and they differ in kind

**Option A — minimal relocation.** Move the claude cluster (three names, §2) to its own owner behind three
operations. Byte-identical bodies. Leaves the other six handles where they are, and leaves the pattern
half-owned: one detector managed through operations, six through raw module `let`s and a hand-maintained
teardown list.

**Option B — a teardown registry.** One owner exposing `registerTeardown(name, stop)` and
`runAllTeardowns()`. Each start site registers its own stopper:

```js
registerTeardown("codex-turn-detector", startClaudeTurnEndDetector({ … }));
```

The `let __stopX = () => {}` declarations disappear entirely — an unstarted detector is simply not in the
registry, so the no-op default has nothing to defend against. `cleanupOnExit`'s seven try/catch lines become
one call. Adding an eighth detector stops being a three-place edit (declare, assign, remember to tear down),
which is the failure mode this shape invites: **a detector added without its teardown line leaks a timer for
the process lifetime, and nothing would notice.**

**Option B is a RESHAPE, not a relocation.** The `start*({ … })` argument objects move byte-identical, but
the declarations and the teardown do not — there is nothing to be byte-identical to, because the registry
replaces them. That needs an explicit ruling against the series' structural-only rule, which is why this is
a packet and not a slice.

**My recommendation is B**, with A as the fallback if the reshape is out of scope. B is the only one that
removes the leak-by-omission hazard, and the operator's mid-series widening ("we want the best architecture…
we just have to do it safely and well, we have to review and test") reads as licensing it. But it is the
reviewer's ruling, not mine, and A is a real improvement on its own.

## 5. Sequencing either way

1. **`__runtimeAdapter` owner** — prerequisite for both options, and independently useful (16 readers, six
   concerns, a `let` assigned once at load: the same mutable-binding shape as the gateway config owner just
   accepted).
2. Then A or B per the ruling.
3. Then registration extraction with `ensureDispatchLoop` injected, unchanged from the accepted sequence.

None of the three touches `runDispatchLoop`.

## 6. What I have NOT established

- Whether the three unconditional heartbeat handles (269/306/323) have start-order dependencies on each
  other. They are adjacent and independent on inspection, but "on inspection" is what produced the
  `__runtimeAdapter` error, and a registry changes nothing about start order anyway — it changes teardown.
- Whether `cleanupOnExit`'s teardown ORDER matters. **Measured, and the two orders differ for four of the
  seven**, so this is not hypothetical:

  | position | teardown order (L732–738) | start order (assignment line) |
  |---|---|---|
  | 1 | `handleHeartbeat` | `handleHeartbeat` (269) |
  | 2 | `turnBusyHeartbeat` | `turnBusyHeartbeat` (306) |
  | 3 | `livenessHeartbeat` | `livenessHeartbeat` (323) |
  | 4 | **`gatewayProbe`** | **`claudeTurnEndDetector`** (378) |
  | 5 | **`residentHermesTurnDetector`** | **`codexTurnDetector`** (454) |
  | 6 | **`claudeTurnEndDetector`** | **`gatewayProbe`** (587) |
  | 7 | **`codexTurnDetector`** | **`residentHermesTurnDetector`** (640) |

  A registry replaying registration order would therefore tear down in a different sequence than today. The
  three heartbeats agree; the four conditional detectors do not. Whether that matters is a behavioural
  question I have not answered — the gateway probe currently stops BEFORE the hermes turn detector that
  depends on a gateway, and reversing that is exactly the kind of thing that only shows up under load.
  **Option B must therefore carry an explicit teardown order, not inherit registration order.** If the
  reviewer picks B, I would characterise this first as its own step rather than assume the sequence is
  incidental.
- Whether anything outside `server.js` starts a detector. Measured no, but only within `mcp/stdio/*.js|mjs`.

## 7. Asking

1. Accept the §1 correction — `__runtimeAdapter` gets its own owner, and is not part of the detector group?
2. Option A or Option B? If B, do you want the teardown-order question in §6 characterised first as its own
   step, which is what I would do unprompted?
3. Is §5's ordering right, with the adapter owner going first as an ordinary slice like the gateway one?
