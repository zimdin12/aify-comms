# `hermes-managed-host.js` — mini-packet

**Status:** submitted for review. No extraction performed. Measured on `3a93136c`.

The reviewer's condition 1 for this file is a mini-packet before any slice: function inventory, export
surface, import/test consumers, and a proposed first subject cluster. Condition 9 adds that no "movable
surface" number may be quoted without running the classifier against this actual target — the `app.js`
figure needed three corrections, so the numbers below come from this file, not from the last one.

---

## 1. Inventory

```
3,017 lines · 46 top-level functions · 27 exported · 0 classes · 18 import statements
28 module-scope bindings, of which 9 are env-derived
0 module-scope `let`/`var`
```

**Node-side, so no browser globals and no inline-handler hazard** — the two constraints that shrank
`app.js`'s movable surface from 1,090 to 141 do not apply here at all. Every one of the 46 functions is
transitively free of module-scope mutable state by a name-based scan, which is why this file is the one
that can actually move.

### Consumers — this is a live path, unlike app.js

| consumer | kind | imports |
|---|---|---|
| `mcp/stdio/server.js` | production, live MCP bridge | `openGatewayWsClient`, `makeGatewayReachabilityProbe`, `gatewayIndexUrlFromWs`, `reportGatewayDead` |
| `mcp/stdio/hermes-channel.js` | production | `reportGatewayDead` |
| 5 test files | executing | 26 of the 27 exports |

26 of 27 exports already have an executing test importer. That is the opposite of `app.js` (0 of 0) and it
changes what a slice here buys: **not new coverage, but a smaller unit of code per test.** Worth stating
plainly so the slice is not credited with coverage it did not add.

`classifyClaimError` (16 lines) is the one export with **no consumer at all** — neither production nor
test. Flagged, not touched: proving a JS export dead needs more than a name grep, and that is its own task.

---

## 2. Proposed first cluster — the gateway

Measured closure of the 11 gateway-named exports:

```
14 functions, 478 lines
11 already exported, 3 internal (waitForIndexToken 33, scrapeToken 11, sleep 3)
needs ZERO functions from outside the closure
```

| lines | function | |
|---|---|---|
| 197 | `ensureGatewayHost` | exported |
| 78 | `openGatewayWsClient` | exported, **server.js** |
| 36 | `maybeReEnsureGatewayHost` | exported |
| 34 | `makeGatewayReachabilityProbe` | exported, **server.js** |
| 33 | `waitForIndexToken` | internal |
| 24 | `reportGatewayDead` | exported, **server.js + hermes-channel.js** |
| 21 | `isGatewayConnectRefused` | exported |
| 14 | `gatewayIndexUrlFromWs` | exported, **server.js** |
| 12 | `teardownGatewayHost` | exported |
| 11 | `scrapeToken` | internal |
| 7 | `gatewayUnreachableMessage` | exported |
| 5 | `nextReEnsureBudget` | exported |
| 3 | `sleep` | internal |
| 3 | `shouldApplyGatewayTurnEnd` | exported |

One subject: **starting the hermes gateway host, probing whether it is alive, reporting it dead, and
tearing it down.** It satisfies the reviewer's preference for relocating an already-exported cohesive group
before inventing public exports — 11 of 14 exports already exist and keep their names, and the 3 internals
stay private.

Five functions outside the closure call into it (`deliverRun`, `runDeliveryLoop`, `runResolveSessionCli`,
`runEnsureHostCli`, `installShutdownTeardown`); the host imports the new module and remains a caller.

---

## 3. Two things that must be resolved IN the slice, not after

### 3.1 `_teardownState` is shared mutable state reached through a default parameter

```js
const _teardownState = { done: false };
export async function teardownGatewayHost({ child, state = _teardownState } = {}) { ... }
async function installShutdownTeardown({ ..., state = _teardownState }) { ... }
```

It is never named on the left of an assignment. The mutation happens through the `state` parameter inside
both functions, so **a name-based scan for `X.prop =` does not see it** — my classifier reported zero
mutated module-scope bindings for this file while this object is exactly that. That is the fourth detector
gap in this lane (after inline handlers, browser-global aliases, and `const` objects mutated in place), and
the pattern is the same every time: the state is reached by an alias the scan does not follow.

`teardownGatewayHost` is in the closure and `installShutdownTeardown` is not, so extracting as measured
would put one reader of a shared object on each side of a module boundary. ESM live bindings would keep it
one object if exported, but relying on that is a footgun for the next reader.

**Resolution: `installShutdownTeardown` (22 lines) joins the cluster.** It is teardown, it is the same
subject, and then `_teardownState` has exactly one owner with both its readers beside it. Cluster becomes
**15 functions / 500 lines**.

### 3.2 Env-derived constants are read on both sides

| constant | closure reads | host-side readers |
|---|---|---|
| `GATEWAY_PROBE_TIMEOUT_MS` | yes | none |
| `READY_TIMEOUT_MS` | yes | none |
| `RPC_TIMEOUT_MS` | yes | none |
| `HERMES_CMD` | yes | `resolveHermesPython`, `ensureStableSession` |
| `MACHINE_ID` | yes | `runPollCycle` |
| `RUNTIME` | yes | 5 functions |

The first three follow the cluster (sole readers). The last three have readers on both sides, and there is
no good answer inside two modules: leaving them in the host makes the new module import UPWARD from the
file it is draining, which is the inversion the Python `test_leaves_do_not_import_the_carrier` gate exists
to prevent; moving them makes the host import them back, which is fine, but they are not gateway concerns.

**Resolution: a neutral `mcp/stdio/hermes-env.mjs`** owning the env-derived constants both sides read, with
each importing it. Same shape as `api_core/liveness.py` owning `TURN_BUSY_BACKSTOP_SECONDS` — a constant
with readers on both sides of a boundary belongs to neither.

So the slice is **two modules**: `hermes-env.mjs` (constants) and `hermes-gateway.mjs` (the 500-line
cluster). Stated up front because a one-module version would either duplicate a constant or invert a
dependency, and both are worse than an extra file.

---

## 4. Proof obligations, per the reviewer's conditions

- **Reconstruction must handle pre-existing exports.** The `app.js` prover assumes the single declared
  substitution is a prepended `export `. Here 11 functions ALREADY say `export function`, so their spans
  are byte-identical with no substitution at all, and the prover must not strip an `export ` that was
  always there. It needs a per-item flag for whether `export ` was added or pre-existing, and a test that a
  pre-existing export round-trips unchanged.
- **One pristine fixture** for `hermes-managed-host.js`, tracked, with the accumulating plan — the shape
  the reviewer approved for `app.js`.
- **Real tests import and execute** the moved surface. Since 26 exports already have executing importers,
  the new module's test must add assertions the host's tests do not already make (degenerate inputs on the
  gateway URL/message helpers, and the re-ensure budget arithmetic), or it adds a file and no evidence.
- `node --check` on the host, both new modules, and the tests; then `cd mcp/stdio && node tests/run-all.mjs`.
- **Blast radius: `bridge/server live path`-adjacent.** `server.js` imports four of these at bridge startup.

### 4.1 Deployment note, which a container rebuild does NOT cover

`mcp/stdio/` runs on the HOST, not in the container. Per CLAUDE.md, a change here needs
**`install.sh` re-run** (the installer copies `mcp/stdio` into `~/.aify-comms/`, and every wrapper points at
that native copy) **and a wrapper relaunch** — a running wrapper keeps executing the code it loaded at boot.
`aify-comms doctor` reports this as `bridge-installed` (commits touching `mcp/stdio/` since the installed
marker) and `bridge-current` (a live bridge whose self-reported build ≠ repo HEAD).

So a green `node tests/run-all.mjs` proves the code is correct and proves nothing about what is RUNNING.
I will not run `install.sh` or relaunch wrappers as part of a refactor slice — there are live managed
agents, and superseding their bridges to land a structural change is not a trade I should make unilaterally.
The slice will state that the change is inert until the operator reinstalls, which is the same position
every other `mcp/stdio` change in this series has been in.

---

## Open question

Nothing blocking. The two resolutions in §3 are my proposals rather than measurements; if the reviewer
prefers `_teardownState` exported from the gateway module instead of pulling `installShutdownTeardown` in,
or prefers the shared constants staying in the host, say so and the slice follows that instead.
