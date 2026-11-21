# `pi-session.js` — file packet

**Status:** submitted for ruling. Measured at `fb3208f3`. **No extraction performed.**

`pi-session.js` is **1,300 lines** and one of the five non-test source files still over the operator's limit.
It was previously recorded as being at the relocation ceiling on the grounds that "the `PiSession` class is
960 of them". That is true and it was the wrong conclusion, because **340 lines sit outside the class** and
nobody had measured them.

---

## 1. Structure, measured

20 top-level items, 1,228 lines. The class is 960 of those; the other 268 lines are 19 functions in three
clusters, plus 11 module bindings.

| region | lines | what |
|---|---|---|
| L22–L32 | 11 | module bindings (pool Map, size caps, timeouts, truncation marker) |
| L34–L233 | 200 | **formatting**: pi events → terminal frames |
| L235–L267 | 33 | **timeouts and a deferred helper** |
| L269–L1228 | 960 | `class PiSession`, 36 methods |
| L1230–L1299 | 70 | **the session pool**: acquire/get/shutdown + test accessors |

## 2. Cluster 1 — formatting (L34–L233, ~200 lines + 4 constants). CLEAN.

Turns a pi event into a terminal frame: tool inputs and results briefed, token usage rendered, ANSI applied.
The pi equivalent of `tool-response-format.mjs`.

- **Calls no class method and reaches no class state** — measured, zero contact in that direction.
- One import: `extractPiAssistantText` from `runtimes.js`, already a leaf.
- Four constants (`MAX_PI_ERROR_CAPTURE_CHARS`, `MAX_TOOL_INPUT_BRIEF_CHARS`,
  `MAX_TOOL_RESULT_BRIEF_CHARS`, `PI_TRUNCATION_MARKER`) are declared above the region and used **only**
  inside it, so they travel with it.

**But the boundary is not clean-cut, and the export surface is five names, not one.** Measured, the class
reads five of these:

| name | refs inside | refs in the class | verdict |
|---|---|---|---|
| `ANSI` | 20 | **16** | must export |
| `colorize` | 14 | **12** | must export |
| `appendBounded` | 1 | **6** | must export |
| `boundText` | 2 | **3** | must export |
| `formatPiEventAsTerminalFrame` | 1 | **1** + a test | already exported |
| `briefJsonInline` | 8 | 0 | private |
| `formatToolInputBrief` | 2 | 0 | private |
| `formatToolResultBrief` | 3 | 0 | private |
| `formatTokenUsage` | 3 | 0 | private |

Five exports is wider than this lane's usual one, and each has a real consumer outside the module — which is
the reviewer's stated test for an export. Flagging it rather than hiding it: if five is too many, the
alternative is leaving `ANSI`/`colorize`/`boundText`/`appendBounded` behind, which splits the formatting
subject across two files and is worse.

**This cluster has never been tested.** `formatPiEventAsTerminalFrame` is 107 lines of branching over pi
event types, exported, and reachable only through `pi-session-terminal.test.js` — which imports it, so it has
*some* coverage. The seven helpers have none.

## 3. Cluster 2 — timeouts and deferred (L235–L267, ~33 lines + 2 constants). CLEAN.

`createDeferred`, `idleTimeoutFor`, `startupTimeoutFor`, `timeoutFor`, plus `DEFAULT_IDLE_TIMEOUT_MS` and
`STARTUP_TIMEOUT_DEFAULT_MS`.

**All four functions are called from inside the class**, so all four export. No class state is read. Clean
relocation, small, and the timeout resolution is worth a test — `idleTimeoutFor` has a 24-hour default and
these values decide when a pi session is considered dead.

## 4. Cluster 3 — the pool (L1230–L1299, ~70 lines). **BLOCKED BY A CYCLE.**

Seven functions, all region-only. But the Map they manage is not:

- `piSessionPool` (declared L22): 7 refs in the pool region, and **4 in the class** — L802, L803, L1194,
  L1195. The class removes itself from the pool on teardown and on stop.
- `acquirePiSession` constructs `new PiSession(...)`.

So the pool needs the class and the class needs the pool. **Moving the pool creates a circular import**, and
the fix is a reshape rather than a relocation: either the pool exports a `deregister(key)` the class calls
(still circular, since the pool constructs), or `PiSession` takes an on-exit callback and stops knowing about
the pool at all. The second is the right shape and it is a behavioural edit to the class's constructor
contract.

## 5. What this buys, honestly

| | lines |
|---|---|
| now | 1,300 |
| after cluster 1 | ~1,096 |
| after cluster 2 | ~1,063 |
| after cluster 3 (blocked) | ~993 |

**Clusters 1 and 2 are clean and take it to ~1,063 — still over.** Clearing 1,000 needs either the pool
cycle resolved, or extract-method inside the class. The class's four largest methods are `_onStdoutLine`
(89L), `_onChildExit` (75L), `ensureStarted` (52L) and `_maybeHealMissingSessionForTurn` (48L); the Python
side established that extract-method is in scope for this series, and the JS side has no equivalent gate.

So: **this file CAN clear 1,000, and the previous "relocation ceiling" reading was wrong** — but not by
relocation alone.

## 6. What I have NOT established

- Whether `ANSI` and `colorize` belong in the formatting module or in a smaller shared owner of their own.
  They are terminal-rendering primitives with 36 combined references; I have not measured whether anything
  outside this file wants them.
- Whether the four private helpers are genuinely untested or covered incidentally through
  `formatPiEventAsTerminalFrame`. Their behaviour under degenerate input is unknown, and given this lane's
  record I would expect at least one placeholder-leak.
- Anything about the class's internals beyond method sizes. No extract-method claim is made here.

## 7. Asking

1. Accept that `pi-session.js` is not at a relocation ceiling — 268 of its 1,300 lines are outside the class
   and two clusters totalling ~233 are clean?
2. Clusters 1 and 2 as two ordinary slices, on the `tool-response-format.mjs` precedent? Cluster 1 needs your
   view on the five-export surface.
3. Cluster 3's cycle — do you want a separate packet for the on-exit-callback reshape, or should the file
   stop at ~1,063 and clear the last 63 by extract-method inside the class?
