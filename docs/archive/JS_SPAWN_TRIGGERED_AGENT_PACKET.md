# `spawnTriggeredAgent` — seam packet

**Status:** submitted for ruling. Measured at `cc8f9de0`. **No extraction performed.** Written to the
reviewer's seven headings after they ruled this must not move as a casual channel prerequisite.

`spawnTriggeredAgent` is local-mode auto-start: a `comms_send` or `comms_channel_send` arrives for an
agent with no live worker, and this launches one. **84 lines, L4495–L4578, zero `await`s, two early
returns, no `try` block.** It blocks `comms_send` (135L) and `comms_channel_send` (111L).

---

## 1. Direct dependencies

| name | source | note |
|---|---|---|
| `normalizeSessionMode` | `session-mode.mjs` | owned, `cc8f9de0` |
| `deliverMessage`, `readAgents`, `writeAgents` | `local-store.mjs` | owned |
| `httpCall` | `aify-service-endpoint.mjs` | owned |
| `normalizeRuntime`, `canLaunchRuntime`, `launchRuntimeRun` | `runtime-markers.js` | already a leaf |
| `randomUUID` | `crypto` | builtin |
| `parseJson` | **server.js, 6 refs** | unowned |
| `__markControllerStart` | **server.js, 4 refs** | unowned — see §4 |
| `LOCAL_RUNTIME_STATE` | **server.js Map, 4 refs** | unowned — see `JS_BRIDGE_AGENT_STATE_PACKET.md` |

**Three unowned dependencies, not one.** Everything else already has an owner, which is why this became
measurable at all.

## 2. State touched

- **`LOCAL_RUNTIME_STATE`** — read once for the merge, written inside the `onRuntimeState` callback.
- **`ACTIVE_CONTROLLER_PROMISES`** — not touched directly; `__markControllerStart` owns it.
- **`agents.json`** — read and rewritten inside `onRuntimeState`, via `readAgents`/`writeAgents`.

### The runtime-state authority question, stated because it is a decision and not an accident

```js
const baseState = parseJson(targetInfo.runtimeState, {});
const runtimeState = { ...baseState, ...(LOCAL_RUNTIME_STATE.get(targetId) || {}) };
```

**In-memory wins over persisted.** The Map is treated as fresher than the registry file, which is
correct while the process that wrote it is the process reading it, and is exactly the assumption that
breaks across a bridge restart — the Map is empty then, so the file becomes authoritative again. Nothing
states this today and nothing tests it.

## 3. Start / skip / failure matrix

| condition | outcome |
|---|---|
| resident + codex + `resident-run` capability + `sessionHandle` | START (`executionMode: "resident"`) |
| managed + `managed-run` capability | START (`executionMode: "managed"`) |
| resident, but no `sessionHandle` | SKIP → error message to sender: "re-register that live session first" |
| any other shape | SKIP → error message to sender: "not configured as a launchable managed session" |
| runtime fails `canLaunchRuntime` | SKIP → error message: "does not support active dispatch" |
| `launchRuntimeRun` throws | **UNHANDLED** — no `try` block; propagates to the caller |
| controller promise rejects | logged to stderr, nothing else |

**Every skip path returns `undefined`, and so does the success path.** The function reports failure by
delivering a message to the sender, never to its caller. Both call sites ignore the return value, so
today that is consistent — but it means a caller cannot know whether a worker started, and any future
caller that wants to know has no way to ask.

## 4. Controller-start ownership

`__markControllerStart` (server.js:322) adds a promise to `ACTIVE_CONTROLLER_PROMISES` and removes it on
settle. That set is what holds the turn-busy heartbeat while a controller runs — it exists because of
the operator-observed "working flapping to online during long turns".

It has **three call sites**: a turn handle (L1059), the main dispatch loop (L3344), and this function
(L4571). So it is NOT group-exclusive, and it must not move into a spawn leaf. Either it gets its own
owner alongside `ACTIVE_CONTROLLER_PROMISES` — the set and its only mutator, which is the clean shape —
or a spawn leaf takes it as an injected dependency.

**My recommendation: its own owner, with the set.** One name, one mutator, one subject (which controller
promises are outstanding), and three callers that all just report a start. That also gives the turn-busy
heartbeat its first direct test.

## 5. Double-start / double-mark hazards

**Nothing guards re-entry.** There is no check for an existing controller for `targetId`, no key in
`LOCAL_RUNTIME_STATE` marking a launch in flight, and no dedupe in `__markControllerStart` beyond the
`Set`'s own identity semantics (two different promises are two different entries). Two sends arriving
for the same idle agent would launch two controllers and mark both.

I have **not** established whether that is reachable — it depends on whether the two call sites can
overlap for one target, which is a question about `comms_send`'s and `comms_channel_send`'s own guards.
**Recorded as an open hazard, not a claimed bug.** Establishing it is characterization work and belongs
before any reshape, not inside a relocation.

**A second hazard I am more confident about.** `onRuntimeState` does a read-modify-write of the WHOLE
registry:

```js
const registry = readAgents();
registry.agents[targetId].runtimeState = merged;
writeAgents(registry);
```

Two controllers for *different* agents settling close together each read the full registry, modify their
own entry, and write the whole file back. The later write loses the earlier one's change. `writeAgents`
is a plain `writeFileSync` with no locking and no read-back. This is not specific to this function —
every `readAgents`/`writeAgents` pair has it — but this is the path most likely to run concurrently,
because it fires per runtime-state update per controller.

## 6. Negative proof

- Does **not** reach `runDispatchLoop`, `comms_register`, or the claim path.
- Does **not** touch `REMOTE_AGENT_STATE`, `ACTIVE_RUNS` or `CONSECUTIVE_FAILURES` — the three Maps in
  the other packet. Its state contact is `LOCAL_RUNTIME_STATE` alone, which is exactly why that packet
  proposes leaving that Map out of the reset-together group.
- Is **not** async: zero `await`s. Whatever moves, no await ordering can change.

## 7. Characterization needed before movement

None of this is currently covered. In dependency order:

1. Each of the three SKIP paths delivers its specific message to the SENDER, not the target, and starts
   nothing (`launchRuntimeRun` never called).
2. Both START paths pass the right `executionMode` and the merged `runtimeState`.
3. The merge precedence in §2: in-memory over persisted, and the post-restart case where the Map is
   empty.
4. `onRuntimeState` writes BOTH stores and the merged value is what lands in each.
5. `onReady` PATCHes `/agents/<id>/ready` and a failure there is swallowed (it is `.catch(() => {})`,
   deliberately best-effort).
6. `__markControllerStart` is called exactly once per successful start, and not at all on a skip.

All six are reachable with a fake `launchRuntimeRun` and a scratch store, in the shape the three
accepted tool-group tests already use.

## 8. Asking

1. Does `__markControllerStart` + `ACTIVE_CONTROLLER_PROMISES` get its own owner as §4 recommends, or
   does a spawn leaf take it injected?
2. `parseJson` — its own tiny owner (6 refs, pure, no deps), or injected? It is generic enough that a
   `session-mode`-style tiny leaf feels right, but it is the third tiny leaf in a row and you have an
   open over-splitting concern.
3. Characterization first (§7) and relocation second, as two reviewed steps? That is what I would prefer
   given the two hazards in §5.
4. The `readAgents`/`writeAgents` lost-update hazard is not this function's fault and is not fixable
   inside a structural slice. Separate packet, or fold into the behavioural batch with comms_search's
   local branch, comms_unsend's cross-inbox delete, `AIFY_AGENT_ROLE` and `validateName(null)`?
