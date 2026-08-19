# The multi-service stack, traced — what works, what breaks, and the tier nobody has built

**Question.** Several services — `aify-comms`, `aify-dashboard`, `aify-project-graph`, more later —
all talking to one shared wrapper. Does the current stack support it, and does the bridge need
changes because the wrappers moved to the `HARNESS_*` contract?

**Short answers.** The MCP half already works and needs nothing. The identity half does not. And the
thing that actually blocks a second service is neither: it is that **spawning lives inside
aify-comms**, so any other service either duplicates PTY ownership on the same host or depends on
aify-comms to start agents for it.

Traced from the code on 2026-08-19 at `0acf7b14`, not recalled. The commit that published this
file adds nothing but this file, so the tree that was traced and the tree this document ships in are
the same code — which is the only reason a provenance sha written before the commit is honest.

---

## What happens today, step by step

1. **Install bakes one endpoint.** `install.sh --client claude <url>` renders
   `@@ENDPOINT@@` into the launcher as a fallback. One URL, one service, chosen at install time.

2. **The launcher resolves and exports.** `HARNESS_ENDPOINT` = flag → `HARNESS_ENDPOINT` →
   `AIFY_COMMS_URL` → the baked value. It exports `AIFY_COMMS_URL`, `AIFY_AGENT_ID`,
   `AIFY_AGENT_ROLE`, `AIFY_SESSION_MODE`, `AIFY_CHANNELS_ENABLED`, then writes an MCP config and
   execs the runtime.

3. **The MCP config is already plural, and it is the RUNTIME'S, not the wrapper's.** In the default
   path the launcher writes no MCP config at all: the runtime reads its own user-scope config, which
   `install.sh` registered the service into. `mcpServers` is a MAP holding every server the operator
   has, aify's beside unrelated ones. The launcher writes its own two-entry file ONLY under
   `AIFY_CLAUDE_STRICT_MCP=1`, and that path excludes everything else. See the correction above.

4. **The runtime loads the bridge**, which registers the agent against the endpoint it was given.

5. **The bridge can spawn more agents.** In its environment role it builds a child environment with
   `terminalChildEnv()` and launches the launcher again as a PTY child.

---

## CORRECTION (2026-08-19, after the review) — Finding 1 was overstated

The operator asked whether the stack works the way he described it, and checking that rather than
re-reading my own trace found the overstatement. **Finding 1 below is true about the wrapper's export
chain and about strict mode. It is NOT true of the default path**, and the difference decides most of
this document's practical answer.

`aify-service-endpoint.mjs`, `aify-http.mjs`, `claude-channel.js` and `hermes-channel.js` all resolve
the endpoint as `CLAUDE_MCP_SERVER_URL || AIFY_SERVER_URL`. **The bridge never reads
`AIFY_COMMS_URL`.** That name is read by the stop-gate hook and by doctor, not by the code that
decides where the bridge connects.

And `register_stdio_server()` in `install.sh` writes the endpoint into the RUNTIME'S OWN MCP config as
a per-server env block — `claude mcp add --scope user --env AIFY_SERVER_URL=... --env
CLAUDE_MCP_SERVER_URL=...`, codex's TOML, hermes' config — which is exactly the pair the bridge reads.

So **per-service endpoints already work in the default path.** Each registered service carries its own
URL in its own entry. The inheritance chain I described is the fallback, not the mechanism. On this
host the user-scope config holds five MCP servers, of which `aify-comms` and `aify-comms-channel`
carry that env pair; the other three are unrelated servers sitting alongside, which is the shape a
second service would join.

**What I missed, and it is the real client-tier breaker:** `AIFY_CLAUDE_STRICT_MCP=1` passes
`--strict-mcp-config` with the hand-written two-entry file below. That flag DELETES every other MCP
server from the session. The escape hatch for the Claude MCP init race (upstream #38462, #21341) is
therefore precisely the switch that forbids multi-service. Anyone running strict mode has one service
and cannot have two, whatever the registry says.

**One thing this correction does NOT settle.** Whether a runtime's per-server env block beats the
inherited environment is PROVEN for codex — `install.sh`'s own comment records that the block REPLACES
inherited env — and ASSUMED for claude. It has never been exercised, because no session has ever had
two entries carrying DIFFERENT urls. The day a second service exists, that is the first thing to test.

Findings 3 and 4 are untouched by this correction.

---

## Finding 1 — the endpoint reaches a spawned agent by INHERITANCE, not by decision

`terminalChildEnv()` opens with `{ ...baseEnv, ... }` and then sets `AIFY_AGENT_ID`,
`AIFY_COMMS_AGENT_ID`, `AIFY_AGENT_ROLE`, `AIFY_AGENT_CWD`, `AIFY_SESSION_HANDLE`,
`AIFY_SESSION_MODE`, `AIFY_MANAGED_VIA_WRAPPER` explicitly. (`AIFY_COMMS_AGENT_ID` is the fallback
name and carries the SAME value, which is Finding 3 in miniature: two names, one identity.)

**It never sets `AIFY_COMMS_URL`.** The spawned agent gets the endpoint because the bridge process
inherited it from the launcher that started the bridge, and passes its whole environment down.

So the chain is: bake → launcher exports → bridge inherits → child inherits.

For one service this is invisible and works. For several it has two consequences:

- **Nothing NAMES the service.** There is no `AIFY_SERVICE`. A spawned agent cannot tell which
  service it belongs to, and cannot be told to belong to a different one — the value simply arrives.
- **The bridge cannot decide.** A spawner that wanted to start an agent against `aify-dashboard`
  rather than itself has no field to say so; it would have to mutate its own inherited environment.

## Finding 2 — the bridge needs NO changes for the contract, and that is worth stating

I expected to find breakage here and did not. `terminalChildEnv()` sets exactly the legacy `AIFY_*`
names, and every `HARNESS_*` input falls back to its legacy name by design. A bridge-spawned wrapper
therefore resolves identically before and after Phase 2. The `--check` path is not on any spawn route,
and exit 78 cannot fire from a spawn because `AIFY_COMMS_URL` is read with `:-`, so an empty inherited
value falls through to the baked one rather than being treated as a cleared endpoint.

**The bridge change multi-service needs is not a fix, it is new capability:** naming the service and
carrying per-service identity.

## Finding 3 — identity is per-AGENT, and needs to be per-SERVICE

`AIFY_AGENT_ID` is one value. An agent registered with `aify-comms` as `coder-1` and with
`aify-project-graph` as something else has no way to express that: the launcher exports one id, and
each loaded bridge reads the same one.

For read-only services this may not matter. For any service that registers agents, tracks turns, or
owns dispatch, it does — two services would fight over one identity, or silently assume the other's.

## Finding 4 — the real blocker: SPAWNING is a service capability, not a client one

This is the one that decides the shape of everything else.

The wrapper is client tier: it launches one runtime and exits. It has no opinion about services and
does not need one. But **starting an agent** — allocating a PTY, owning the process, reaping it,
adopting terminals, writing `terminal_sessions` — lives in `mcp/stdio`'s environment-bridge role,
which is aify-comms' code, aify-comms' database and aify-comms' reconcilers.

So when `aify-project-graph` wants to start an agent, there are three possible answers and no fourth:

1. **It runs its own environment bridge.** Two spawners on one host, each owning PTYs, each reaping
   by its own rules. This repo already has recorded incidents from *one* spawner colliding with
   itself (per-agent gateway ports derived from the agent id, kill-prior reaping by port, orphan
   reapers keyed on bridge instance). Two would be worse, and the failures would be cross-repo.
2. **aify-comms spawns on everyone's behalf.** Works today and makes aify-comms a hard dependency of
   every other service, which is the coupling the split exists to remove.
3. **The environment tier comes out of aify-comms**, as its own component that any service can ask
   to start an agent. This is the three-tier target the operator described — client / environment /
   server — and it is the only one of the three that scales without either duplication or dependency.

**Nothing has been built for (3).** Phase 2 extracted the CLIENT tier. The environment tier is still
inside aify-comms, and the wrapper contract deliberately says nothing about the environment↔server
edge, calling it a later subject. That was the right scope decision and it is also exactly the gap
that a second service walks into.

---

## So: is everything done?

**For one service, yes.** The stack is coherent and the contract holds.

**For several, no**, and the missing pieces are these, smallest first:

| # | Missing | Size |
|---|---|---|
| 1 | A registry (`~/.aify/services.json`) that services write at install and the launcher reads at launch | small |
| 2 | The launcher emitting one MCP server entry per registered service | small — the map is already plural |
| 3 | `AIFY_SERVICE` naming which service a session belongs to, set explicitly by a spawner instead of inherited | small |
| 4 | Per-service identity, probably `agentId` inside each registry entry, with `HARNESS_IDENTITY` kept as the single-service override | medium — it changes the contract |
| 5 | The environment tier extracted so any service can request a spawn without owning PTYs | **large — v0.6 Phase 8** |

1 to 3 are additive and safe. 4 changes a contract that four wrappers and a live fleet depend on. 5 is
a release of its own.

## What I would NOT do

- **Give each service its own environment bridge.** Item 4 in this repo's known-issues history is
  full of one spawner colliding with itself; two independent spawners on one host is that class of
  bug with a repo boundary in the middle of it.
- **Bake a second endpoint into the launcher.** That is the current design's failure mode, doubled:
  last installer wins, silently, and re-running either installer flips it back.

---

## Reviewed

comms-senior-dev attacked all four conclusions at the frozen tree and returned **APPROVE**, having read
`terminal-env.js`, `terminal-control-loop.mjs`, the wrappers, the launcher/template tests and
`launch-identity.mjs`, and run the focused wrapper suite (39 pass / 0 fail).

Two things worth keeping from the review because they are stronger than what I wrote:

- On Finding 1, `terminal-control-loop.mjs` overrides `AIFY_AGENT_ID` after the env builder runs for
  wrapper-managed children — and still never touches the endpoint. That is a second site that had the
  chance to set it explicitly and did not, which makes the inheritance claim firmer, not weaker.
- On Finding 3, `launch-identity.mjs` COLLAPSES `AIFY_AGENT_ID` and `AIFY_COMMS_AGENT_ID` into one
  exported id. So the second name is not a spare slot a second service could use; the collapse is
  where per-service identity would have to be introduced.

Both refusals were upheld. The one change to the plan: items 1 to 3 in the table are plumbing, and
only items 4 and 5 change a contract or move ownership. That is the line to hold when this is picked
up — the first three can ship without a decision, the last two cannot.
