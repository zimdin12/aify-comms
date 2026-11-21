# The bridge's in-memory per-agent state — owner packet

**Status:** submitted for ruling. Measured at `fc8bcda7`. **No extraction performed.**

Four module-scope `Map`s in `mcp/stdio/server.js` are the last unowned things standing between the
v0.5.4 tool-group work and the lifecycle, channel and messaging groups. This packet measures them and
proposes one owner. It changes no code.

---

## 1. Why these are the blocker

`comms_clear` (98L) and `comms_remove_agent` (30L) both need `forgetRemoteAgent`, which reads three of
these Maps. `comms_send` (135L) and `comms_channel_send` (111L) both need `spawnTriggeredAgent`, which
reads the fourth. Between them that is **four tools and 374 lines** that cannot move, plus the two
lifecycle tools that would otherwise be extractable now (`comms_restart` 50L, `comms_delete_session`
32L) but whose group would ship with holes — their own descriptions cross-reference the blocked members.

## 2. The ledger

| Map | decl | refs | mutation sites | what it holds |
|---|---|---|---|---|
| `REMOTE_AGENT_STATE` | `server.js:859` | 21 | 7 | what this bridge believes about each agent it serves |
| `ACTIVE_RUNS` | `server.js:860` | 11 | 5 | which dispatch run is live per agent, with its controller |
| `CONSECUTIVE_FAILURES` | `server.js:919` | 9 | 8 | claim-failure counter driving dispatch backoff |
| `LOCAL_RUNTIME_STATE` | `server.js:861` | 4 | 1 | local-mode runtime record per agent |

All four are `const X = new Map()`. None is ever reassigned — the binding is constant and the contents
mutate, which is the distinction that makes them movable at all (see §4).

## 3. THE FINDING: three of them share one lifecycle

`comms_restart` clears them **together**, in three consecutive lines:

```
5077:        REMOTE_AGENT_STATE.clear();
5078:        ACTIVE_RUNS.clear();
5079:        CONSECUTIVE_FAILURES.clear();
```

That is not a coincidence of authorship. They are the bridge's per-agent in-memory state, and a reset
that cleared one and not the others would leave the bridge believing in a run whose agent it has
forgotten, or backing off for an agent it no longer serves. **Three names, one invariant, cleared as a
unit** — which is the strongest available argument that they want a single owner rather than three.

`LOCAL_RUNTIME_STATE` is **not** in that clear. It is local-mode only, has one writer, and is not part
of the reset. On the evidence it is a different subject and this packet does not propose moving it with
the other three.

## 4. Why moving a mutated `Map` is safe here, and where it would not be

ESM module state is a **per-process singleton**, and an imported binding refers to the same object. So
`REMOTE_AGENT_STATE.set(...)` executed in `server.js` against an imported Map mutates the one instance,
exactly as it does today. This is the same property the endpoint leaf's failover latch rests on, which
the reviewer already accepted.

It would NOT be safe if any of these were **reassigned** (`X = new Map()`), because an importer cannot
rebind an imported name — that is a `SyntaxError`, so it would fail loudly rather than silently, but it
would block the move. Measured: **zero reassignments**. Every mutation is `.set`, `.delete` or `.clear`
on the existing object.

It would also not be safe for state captured in a **closure** rather than declared at module scope —
the distinction that stopped the hermes delivery loop from being relocated. These are module-scope.

## 5. Proposed owner

`mcp/stdio/bridge-agent-state.mjs`, owning the three reset-together Maps and nothing else.

**What it must NOT become:** a home for the ~30 functions that read them. This is a state owner, not a
service layer. Every current reader stays where it is and imports the Map — which is the same shape as
`local-store.mjs` (paths and their accessors) and deliberately unlike a barrel.

**What should move with them, if anything:** `forgetRemoteAgent` (8L) is the one function whose entire
body is "delete this agent from all three". That is the reset invariant in function form, and leaving it
behind would mean the invariant lives in a different module from the state it protects. I propose it
moves; I am not confident, and it is the question I most want ruled.

## 6. What this buys, stated honestly

Unblocks `comms_clear`, `comms_remove_agent`, and the lifecycle group. Does **not** unblock `comms_send`
or `comms_channel_send` — those need `spawnTriggeredAgent`, which reads `LOCAL_RUNTIME_STATE` and also
calls `__markControllerStart` and `parseJson`. That is a separate packet and this one does not pretend
otherwise.

It also buys the first tests these Maps have ever had. Nothing currently asserts that the three-way
clear stays three-way — a fourth Map added to the reset set, or one dropped from it, is silent today.

## 7. Proof plan

Standard for this lane: byte-identical declarations (they are one line each), every reader repointed
including tests, `node --check`, an assertion that no module reassigns an imported Map binding, a test
that the three-way clear covers exactly the owned set (so adding a Map without adding it to the reset
fails), full `mcp/stdio` run-all, and the deployment disclaimer — host code, inert until `install.sh`
(sequential) plus wrapper relaunch.

## 8. Asking

1. Accept `bridge-agent-state.mjs` as the owner for the three reset-together Maps?
2. Does `forgetRemoteAgent` move with them, or stay in `server.js` and import them?
3. `LOCAL_RUNTIME_STATE` — leave it in `server.js` as this packet proposes, or fold it in on the grounds
   that it is also per-agent runtime state, accepting that it is not part of the reset?
