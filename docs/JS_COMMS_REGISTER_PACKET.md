# `comms_register` — seam packet

**Status:** submitted for ruling. Measured at `61f47c75`. **No extraction performed.**

`comms_register` is the largest single item left in `server.js`: **302 lines**, and the last tool group
that has not been either extracted or explained. This packet measures why it has not moved, and reports a
finding that changes the shape of the problem.

---

## 1. The headline: it is blocked by ONE ten-line function

The transitive closure of `comms_register`'s six direct helpers is **39 functions / 1,381 lines**,
touching **14 distinct mutable module names**. On that number it looks untouchable, and that is how the
original server.js packet excluded it.

Measured per seed, the mass is not distributed:

| seed | closure fns | closure lines | mutable names |
|---|---|---|---|
| `ensureDispatchLoop` | **34** | **1,239** | **21** |
| `resolvedRuntimeConfigForRegistration` | 3 | 78 | 2 |
| `armClaudeTurnEndDetector` | 1 | 51 | 4 |
| `reconcileLocalActiveRun` | 3 | 48 | 0 |
| `normalizeRegistrationCwd` | 1 | 16 | 0 |
| `claimCapturedClaudeSession` | 1 | 13 | 0 |

**`ensureDispatchLoop` accounts for 34 of the 39 functions and 1,239 of the 1,381 lines.** Remove it and
the other five seeds close over **8 functions / 190 lines / 6 mutable names** — a normal-sized problem
rather than the whole bridge.

It is ten lines:

```js
function ensureDispatchLoop() {
  if (!IS_REMOTE || dispatchLoopTimer) return;
  if (!localAgentNeedsDispatchHosting({ agentId: AIFY_AGENT_ID, channelsEnabled: … })) return;
  dispatchLoopTimer = setInterval(() => {
    runDispatchLoop().catch((error) => console.error("[aify] dispatch loop error:", error));
  }, DISPATCH_POLL_MS);
}
```

Everything it drags, it drags through `runDispatchLoop` (449 lines) — which the reviewer has already
ruled needs its own packet and must not move as one body. So `comms_register` is not blocked by its own
complexity. It is blocked by a single call that starts the dispatch loop.

## 2. What the residual closure actually is

The 8 functions the other five seeds reach, and the module state each touches:

| lines | function | mutable state |
|---|---|---|
| 51 | `armClaudeTurnEndDetector` | `__claudeTurnDetectorArmed`, `__effectiveAgentId`, `__runtimeAdapter`, `__stopClaudeTurnEndDetector` |
| 35 | `resolvedRuntimeConfigForRegistration` | `AIFY_HERMES_GATEWAY_URL`, `AIFY_HERMES_GATEWAY_TOKEN_ENV_FROM_MARKER` |
| 27 | `resolvedRuntimeMarker` | — |
| 22 | `reconcileLocalActiveRun` | — |
| 16 | `normalizeRegistrationCwd` | — |
| 14 | `clearLocalActiveRun` | — |
| 13 | `claimCapturedClaudeSession` | — |
| 12 | `reportTurnBusy` | — |

Six of the eight touch no module state at all. The state is concentrated in two places:

- **the claude turn-end detector** — four names, all `__`-prefixed, one of which is a stop handle;
- **the hermes gateway configuration** — two names, one with 21 readers across the bridge.

`AIFY_HERMES_GATEWAY_URL` at 21 readers is the one that needs its own owner regardless of what happens to
`comms_register`; it is the same shape as `IS_REMOTE` was.

## 3. The proposal, and why it mirrors a decision already accepted

**Inject `ensureDispatchLoop` into the wrapper**, exactly as `z` is injected: a caller-supplied dependency
rather than an import, for a measured reason stated at the boundary.

`registerRegistrationTool(server, z, { ensureDispatchLoop })`

That is not a trick to make a number smaller. The dispatch loop is a PROCESS-LIFECYCLE concern — one
timer, one process, started once — and registration's relationship to it is "tell it to exist". A
registration module that owned the loop would own the bridge; a registration module that asks for it owns
registration. The reviewer's `z` ruling established that an injected dependency is the right answer when
importing would drag something structural, and this is a much larger instance of the same thing.

**What this does NOT claim.** It does not make `runDispatchLoop` any smaller, does not resolve its packet,
and does not touch the four detector names or the gateway config. Those remain exactly as blocked as they
were; the claim is only that they are the residual problem rather than the whole one.

## 4. Sequencing that follows from the measurement

1. `AIFY_HERMES_GATEWAY_URL` + `AIFY_HERMES_GATEWAY_TOKEN_ENV_FROM_MARKER` get an owner — 21 readers, and
   needed whatever happens next. Frees `resolvedRuntimeConfigForRegistration` (35L) and
   `resolvedRuntimeMarker` (27L).
2. The four `__claude*` detector names get an owner together, on the `controller-activity.mjs` pattern:
   state private, operations exported. `armClaudeTurnEndDetector` is the only seed that touches them and
   `__stopClaudeTurnEndDetector` is a stop handle, so the shape is the same one already accepted.
3. Then `comms_register` with `ensureDispatchLoop` injected, and the six state-free helpers travelling with
   it if they prove group-exclusive (not yet measured — step 1 and 2 change their readership).

Each step is independently reviewable and none requires touching `runDispatchLoop`.

## 5. What I have NOT established

- Whether the eight residual functions are **group-exclusive**. Their readership outside
  `comms_register` has not been counted, and steps 1–2 would change it. No claim either way.
- Whether the four detector names can be made private behind operations, or whether some reader needs the
  raw handle. `controller-activity.mjs` worked because two functions covered every caller; I have not
  checked that here, and asserting it before measuring is the mistake this packet exists to avoid making.
- Anything about `comms_register`'s own 302 lines. This packet is about what it REACHES.

## 6. Asking

1. Accept the finding: `comms_register` is blocked by `ensureDispatchLoop` alone, not by its own closure?
2. Is injecting `ensureDispatchLoop` acceptable on the `z` precedent, or do you want the dispatch-loop
   packet resolved first regardless?
3. Sequencing in §4 — gateway config, then detector state, then the tool. Or a different order?
4. `AIFY_HERMES_GATEWAY_URL` has 21 readers and needs an owner independently of all of this. Worth doing
   as its own slice now, since it is unblocked and useful either way?
