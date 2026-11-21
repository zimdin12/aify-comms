# `runDispatchLoop` — seam packet

**Status:** submitted for ruling. Measured at `58aee3df`. **No extraction performed.** The reviewer ruled
this loop needs its own packet before any movement, in the same category as the hermes delivery loop.

**The headline correction first, because I have told both the reviewer and the operator otherwise:**
`runDispatchLoop` is **NOT** what stands between server.js and its tool region. Measured, exactly **1 of
34** tools drags it through closure — `comms_register`, which the original server.js packet already
excluded as the outlier. The other 33 do not reach it at all.

I claimed it was "the single thing standing between here and the tool region". That was inference from the
888-line state-bound figure, not a measurement, and it is wrong.

---

## 1. What the loop actually touches

`runDispatchLoop` is **449 lines**, L3069–L3517, with 37 awaits and 10 return statements.

**It directly touches ONE module `let`: `dispatchLoopBusy`** — 3 references, 2 writes. That is a
re-entrancy guard, not lifecycle state.

The four `__stop*Detector` handles, which I previously described as something the loop "drags", are
touched by `cleanupOnExit`, **not** by the loop body. They arrive through the closure.

This is the same shape the server.js packet found for the file as a whole: narrow direct contact, wide
transitive inheritance. Getting it wrong here cost two incorrect claims.

## 2. Mutable-state ledger — the closure, not the loop

Closure: **37 functions / 1,251 lines**. Of those, **9 functions / 684 lines** touch module state
directly; **28 functions / 567 lines** are state-free.

| lines | function | module state it touches |
|---|---|---|
| 449 | `runDispatchLoop` | `dispatchLoopBusy` |
| 59 | `runManagedTeardownForBridge` | `confirmedManagedTeardownAgentIds` |
| 51 | `shutdownWithStatus` | `reportEnvironmentOffline`, `residentLostSent`, `shutdownStarted` |
| 39 | `cleanupOnExit` | the four `__stop*Detector` handles |
| 35 | `runManagedTeardownSync` | `confirmedManagedTeardownAgentIds` |
| 16 | `terminateResidentHost` | `residentStopInProgress` |
| 15 | `readReplyCaptureFallback` | `_replyCaptureFallbackCache` |
| 13 | `readManagedViaWrapperRuntimes` | `_managedViaWrapperCache` |
| 7 | `effectiveEnvironmentPayload` | `remoteEffectiveCwdRoots` |

**These are module-scope `let`s, not closure-captured locals.** That is the distinction that separated
server.js from the hermes delivery loop, and it applies here: module state in ESM is a per-process
singleton and CAN be given a narrower owner. The hermes loop's blocker was `let` bindings captured inside
a function, which cannot. **So this loop is not, on the evidence, the same category as the hermes loop —
and I placed it there before measuring.**

## 3. The negative proof the reviewer required

*Will tool/helper extraction drag the loop through closure?*

**No, for 33 of 34 tools.** Only `comms_register` reaches `runDispatchLoop`. Its closure was already
measured at 53 functions and 25 mutable names in the original packet, and it was already excluded from
layer 2 on that basis.

So the loop is not a prerequisite for the tool region. It is a prerequisite for `comms_register` only.

## 4. What this does and does not license

**Does not license moving the loop.** Nothing here proposes that, and the 449-line body with 10 exits and
37 awaits still needs an exit/teardown matrix and characterization before anything moves — the same
standard the hermes packet met before its ruling. That work is not done and is not in this packet.

**Does license removing the loop from the critical path.** If 33 tools do not reach it, layer 2 does not
wait on it. The blocker for the tool region is whatever else those 33 tools' closures contain, which is
the measurement the next packet should make.

## 5. Ordering invariants observed, not yet proven

Recorded as observations because they were read rather than tested:

- `dispatchLoopBusy` guards re-entry; the loop sets and clears it. A second concurrent entry is what it
  exists to prevent, so any reshape must preserve set/clear pairing across every one of the 10 exits.
- `cleanupOnExit` owns the four detector stop-handles and is the only closure member that touches them.
- `runManagedTeardownForBridge` and `runManagedTeardownSync` share
  `confirmedManagedTeardownAgentIds` — one set, two writers, and a split owner would double-teardown.

None of these are currently covered by a test. That gap is the honest reason this packet does not propose
movement.

## 6. Proof plan, if it ever proceeds

Unchanged from the standard the accepted slices met: tracked pristine fixture, reconstruction proof for
byte-identical spans, characterization for every exit path before any reshape, `node --check` on touched
files, focused tests that execute the extracted callers, full `mcp/stdio` run-all, and the deployment
disclaimer — host code, inert until `install.sh` (sequential) plus wrapper relaunch, `bridge-current` red
until then and accurate.

## 7. Asking

1. Accept the correction: the loop blocks `comms_register` only, not the tool region. My "single thing
   standing between here and the tool region" claim was inference, not measurement.
2. Given that — is the loop worth a characterization effort at all right now, or does layer 2 proceed on
   the 33 tools that do not reach it, leaving the loop and `comms_register` together as a later,
   separately-scoped piece?

My recommendation is the second. The loop's 449 lines are real debt, but they are no longer on the path to
anything, and characterizing a 10-exit loop nothing is waiting for is worse value than the remaining
state-free helper mass.
