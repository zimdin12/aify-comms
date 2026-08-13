# The server URL — two derivations, and eight dead guards

**Status:** submitted for ruling. Measured at `bc630703`. **No source changed.**

The reviewer's next step: *"write the server-URL endpoint packet/fix next. Detector remains blocked until
duplicate derivation is resolved."* This is that packet, and the measurement found something larger than the
duplication.

---

## 1. The two derivations

| | `SERVER_URL` — `aify-service-endpoint.mjs:44` | `__serverUrl` — `server.js:263` |
|---|---|---|
| env precedence | `CLAUDE_MCP_SERVER_URL` **first** | `AIFY_SERVER_URL` **first** |
| value when neither is set | `""` | **`"http://127.0.0.1:8800"`** |
| IPv4 loopback coercion | **yes** (`coerceLoopbackToIPv4`) | **no** |
| readers | the HTTP client, failover, mode banner | 10, all heartbeat/turn-event paths |

## 2. THE FINDING: eight guards that cannot fire

Eight of `__serverUrl`'s ten readers are the same shape:

```js
if (!AIFY_AGENT_ID || !__serverUrl) return;   // ×6
if (!__serverUrl) return;                     // ×2
```

**`__serverUrl` has a default, so it is never empty, so `!__serverUrl` is always false.** Verified rather
than reasoned: with both env vars unset it is `"http://127.0.0.1:8800"`; with both set to empty strings it is
the same; the only way the guard could fire is if the default literal were itself falsy.

So the effective guard on those six is `AIFY_AGENT_ID` alone, and on the other two there is no guard at all.
The `__serverUrl` half is dead code in every one of the eight.

**What that means in practice, and I corrected this before submitting.** My first draft said all ten paths
post to the default. They do not, and the distinction matters:

- **Eight of the ten only GATE on `__serverUrl`** — the call they guard goes through `httpCall`, which uses
  the endpoint leaf's own `activeServerUrl()`/`SERVER_URL`. So the URL those use is correct; what is broken is
  only that the gate cannot fire, and in local mode the underlying `httpCall` fails harmlessly because
  `SERVER_URLS` is empty.
- **Two of the ten pass `__serverUrl` as a base URL directly**, bypassing `httpCall` entirely:
  `makeDefaultHandlePoster(__serverUrl, API_KEY)` at L268 and
  `makeDefaultTurnBusyPoster(__serverUrl, API_KEY, BRIDGE_INSTANCE_ID)` at L310. Neither guards internally —
  each strips trailing slashes off whatever it is given and `fetch`es.

So the accurate statement is narrower than my first one and still a defect:

> A bridge in LOCAL mode with an agent id sends its SESSION-HANDLE heartbeat and its TURN-BUSY heartbeat to
> `http://127.0.0.1:8800` — every 30 seconds, for the lifetime of the process — because those two posters were
> given a base URL that is never empty.

Two consequences, and the second is the one that matters:

1. Periodic failed connections to a port nothing is listening on. Swallowed by best-effort catches, so
   invisible. Wasteful, not dangerous.
2. **If anything else is listening on `127.0.0.1:8800`, that agent's session handle and turn-busy state are
   posted to it.** The bridge has no way to know it is talking to the wrong service, because it never
   established that a service was configured at all.

The guards were clearly written to prevent exactly this — someone intended `__serverUrl` to be empty when no
server is configured, which is what `SERVER_URL` does. The default defeated the intent.

**Not live on this host:** neither env var is set here, so `IS_REMOTE` is false and the tools refuse — but
the heartbeats do not consult `IS_REMOTE`, they consult `__serverUrl`. Whether any agent here has run in that
state I have not established and would not claim.

## 2b. SEVERITY, CORRECTED DOWNWARD after reading `install.sh`

I asked in §6 Q3 whether any deployment relies on the internal default, and said only the operator could
answer. The repo answers it, and I should have looked before asking:

- `install.sh:141-143` — if `SERVER_URL` is unset it is assigned `DEFAULT_AIFY_SERVER_URL`, so it is never
  empty by the time anything is written.
- `install.sh:711,716` — every MCP config gets `"AIFY_SERVER_URL"` **and** `"CLAUDE_MCP_SERVER_URL"` set to
  that same value.
- `install.sh:1648-1649` — every wrapper exports both, the second defaulting to the first.

There is also a comment at L116-120 recording an incident where the claude wrapper was the one inconsistent
path, fixed precisely "so every client gets a usable URL".

**So in any supported install both variables are set, to the same value, and `IS_REMOTE` is always true.** That
changes the severity of each item:

| item | reachable in a supported install? |
|---|---|
| eight dead guards | **no** — there is always a server URL, so the guard would not have fired anyway |
| opposite env precedence | **no** — install.sh sets both to the SAME value |
| missing loopback coercion | **YES** — the install prompt accepts whatever the operator types, and `localhost` is the obvious thing to type |

So the defect I led with is real but only reachable OUTSIDE a supported install — someone running
`server.js` by hand, or with a partial environment. I overstated it by not reading `install.sh` first, and I
would rather correct that than let a packet argue for a fix on a premise it does not need.

**The item with live consequence is the coercion gap**, and it is the one I ranked third. An operator who
enters `http://localhost:8800` at the install prompt gets coerced tool calls and uncoerced heartbeats, on
Windows with Docker Desktop, which is this project's platform. That is status flapping with no error in the
logs.

The dead guards are still worth removing — a guard that cannot fire misleads the next reader into thinking the
case is handled — but as cleanup, not as an incident.

## 3. The duplication, which is the smaller half

- **Precedence is opposite.** With both env vars set to different values, the HTTP client talks to one server
  and the heartbeats to the other. Nothing would report the split.
- **`__serverUrl` skips the IPv4 loopback coercion**, whose own comment gives the reason: on Windows with
  Docker Desktop, `localhost` resolves to IPv6 `::1` first and Docker's IPv6 forwarding times out silently.
  So `AIFY_SERVER_URL=http://localhost:8800` yields coerced tool calls and uncoerced heartbeats — status
  flapping with no error, on the exact platform this project runs on. Same half-fix shape as `comms_search`.
- **The default difference is the only intentional one.** `SERVER_URL` must be `""` for `IS_REMOTE` to mean
  anything.

## 4. Proposed fix

Delete `__serverUrl`. Add one export to `aify-service-endpoint.mjs`:

```js
// The base URL to POST to when there IS a server. Empty when there is not — the caller decides.
export const SERVER_URL = …            // unchanged
export const POST_BASE_URL = SERVER_URL;   // same precedence, same coercion, NO default
```

…and change the eight guards from `!__serverUrl` to `!IS_REMOTE`, which is the question they were trying to
ask.

For the two direct posters, **guard at the START site rather than relying on an empty base**. I drafted it the
other way — pass `SERVER_URL` and let an empty base make the poster inert — then read the wrapper and found it
does `try { await postFn(…) } catch { /* best-effort — next tick will retry */ }`. An empty base therefore
produces a THROW that is swallowed on every tick: harmless, but it replaces a wasteful fetch with a wasteful
exception, which is not a fix. So:

```js
const __stopHandleHeartbeat = IS_REMOTE
  ? startSessionHandleHeartbeat({ …, postFn: makeDefaultHandlePoster(SERVER_URL, API_KEY) })
  : () => {};
```

Nothing starts, nothing ticks, nothing throws — and the no-op stopper keeps `cleanupOnExit` unchanged, which
matters because the reviewer parked the teardown-registry reshape and today's explicit teardown order must
survive.

That is one owner, one precedence, one coercion, and a guard that can fire.

**This is a BEHAVIOURAL fix, not a relocation**, and it changes three things:
1. Local-mode bridges stop posting to `127.0.0.1:8800`. **This is the point.**
2. Heartbeat precedence flips to `CLAUDE_MCP_SERVER_URL`-first, matching the HTTP client. Only observable
   when both vars are set to different values, which is already broken.
3. Heartbeats gain the loopback coercion. Only observable with `localhost` configured, where it is a fix.

## 5. What I have NOT established

- Whether any deployment relies on the default. A bridge started with no `AIFY_SERVER_URL` but an agent id,
  against a service on the default port, currently works BY ACCIDENT of that default and would stop. That is
  the one migration risk and I cannot rule it out from here — it needs the operator's answer, not mine.
- **CLOSED since drafting.** `makeDefaultHandlePoster` does not tolerate an empty base — it builds
  `${root}/api/v1/...` with `root = ""`, and `fetch` on a relative URL throws in Node. And the heartbeat
  wrapper DOES swallow it: `try { await postFn(…) } catch { /* best-effort — next tick will retry */ }`. So the
  original §4 would have been harmless but wrong in kind, trading a wasteful fetch for a wasteful exception.
  §4 now guards at the start site instead. Recording the sequence because the first version read as correct
  and only reading the callee showed it was not.
- Whether the four remaining `postTurnStart`/`postTurnEnd` callbacks behave correctly once their gate becomes
  `!IS_REMOTE`. They route through `httpCall`, which already fails harmlessly in local mode, so I expect no
  observable change — but "I expect" is not "I measured", and this is a behavioural fix.

## 6. Asking

1. Accept the finding: the eight `!__serverUrl` guards are unreachable and local-mode bridges post to a
   hardcoded default?
2. Is §4 the right fix, with `!IS_REMOTE` as the guard the eight sites should have used?
3. ~~§5's migration risk~~ — **withdrawn, answered by `install.sh` in §2b.** Every supported install sets
   both variables explicitly, so nothing relies on the internal default and the fix needs no deprecation
   step. I asked the operator a question the repo could answer; §2b is the correction.
4. Should this ship as its own tagged behavioural change rather than inside the structural lane? It is the
   first genuinely behavioural fix I have proposed in this series.
