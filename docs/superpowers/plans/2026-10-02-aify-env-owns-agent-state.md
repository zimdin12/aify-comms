# aify-env owns agent state and lifecycle

Release 0.9.0 in all three repos, after 0.8.0 merges. The operator's direction, 2026-10-02:

> sends when changes or something like that. basically broadcasts. aify-dashboard might want to know also

> aify-comms turns more into purer comms service

and, for this tag ("do it correctly this time"): hermes gateway management in aify-comms while claude's
process side is in aify-env is bad design; each repo's doctor checks only its own responsibilities; a
message to an available agent auto-starting it means aify-comms decides the wake and asks, and aify-env
starts it.

Built on branches `next/0.9-agent-state` in all three repos (worktrees beside each checkout), branched
from the approved 0.8 heads (comms 3811a66f, env df319ad, wrapper 5f3d3018).

## The problem, measured

Read from source on 2026-10-02 at those heads (four independent maps, the load-bearing claims re-checked
by hand).

**Turn state has four producers and two tables in aify-comms, and a third copy in herdr.**

| producer | sends | to |
|---|---|---|
| harness hooks installed by comms `install.sh` (`agent-state-event.mjs`) | turn-start, turn-end, blocked, unblocked | comms `/agents/{id}/turn-start`, `/turn-end`, `/status-event` |
| bridge detectors (`claude-turn-detector-state.mjs`, the codex rollout detector, `hermes-gateway-turn-detector.js`) | the same, with a bridgeId | the same routes |
| managed `turnBusy` heartbeats (`dispatch-loop.mjs`, `hermes-run-reporting.mjs`, `claude-channel.js`) | busy / not busy | `/agents/{id}/heartbeat` |
| aify-env's comms plugin, screen observer (`observeScreen`) | working, idle, blocked, shell | `/terminals/{id}/output` `activity`, read by `host_activity_for` as a 75 s override, managed only |
| the launchers' herdr hook (`aify-herdr-state.sh`) | working, idle, blocked | herdr, directly |

Delivery reads `agent_turn_state` (`_turn_busy_holds_delivery`); the status engine reads
`agent_status_state` (`derive()` via `_gather_status_inputs` and `_compute_live_status_cache`). The same
routes write both, and they can disagree. Process liveness is inferred by aify-comms from terminal rows,
channel-sidecar beats, wrapper-child beats and resident bridge freshness, while aify-env owns the actual
processes (`ProcessRegistry`) and reports only `heldTerminals`.

**aify-dashboard needs an aify-comms connection only for status** (and undefined agents' model/effort).

**Lifecycle is decided in aify-comms.** It builds spawn specs and decides start, stop, restart and
cold-start (`dispatch_start`, `session_control`, `session_ops`, `agent_stop_resume`); aify-env claims and
executes. aify-env never starts an agent from its own definition and has no start, stop or restart verb
(`bin/aify-env-agents.mjs`: list, show, set, remove, import, unlock, recover).

**The environment "stop" control has no claimer.** `control_environment` writes `environment_controls`
rows and `POST /environments/controls/claim` serves them, but nothing in aify-env or the bridge calls
that route (searched 2026-10-02 for the literal and for built `/environments/` paths; the terminal and
dispatch claimers found as the positive control). Stopping an environment changes the service's rows and
nothing on the host.

**aify-env has no route a hook can report to.** Its API (`ROUTES`, `lib/protocol.mjs`) serves processes,
startable/importable agents, start and herdr-space, on 127.0.0.1 with browser-origin refusal and no key.

## What changes, in one table

| | owns after this tag | reads |
|---|---|---|
| **aify-env** | each agent's **state** (turn and process, one derivation); **lifecycle** of defined agents (start, stop, restart from the definition); forwarding state to herdr | launcher hook events, its own process registry, its screen observer, resident records |
| **aify-env service plugins** | carrying state to their service; applying lifecycle requests | the state model, their service |
| **aify-comms** | messages, channels, dispatch runs and reply contracts, files, the chat dashboard; the **status record** = aify-env's state plus messaging facts; the wake decision | state pushed by each host's aify-env |
| **aify-wrapper** | the harness side of one run; hooks report to aify-env only | its definition, aify-env's endpoint |
| **aify-dashboard** | unchanged | state pushed to it directly; no aify-comms connection for status |

## Decisions

**D1. One derivation, in aify-env.** `docs/AIFY_ENV_BOUNDARY.md` says aify-env "must not derive status
of its own — deriving it in two places is how two answers start disagreeing". The reason stands; the
conclusion changes: the derivation moves, and aify-comms' copy is deleted (D7), so there is still one.
Recorded in DECISIONS.md as superseding that line.

**D2. aify-env's state model is a pure function.** Inputs: hook events (turn-start, turn-end, blocked,
unblocked, exited) with `firedAtUs`; process facts from its registry (alive, exit code, reason); its
screen observation (moved from the comms plugin into the core, because it is harness-generic and covers
what hooks miss: claude's Esc interrupt and hermes' non-retryable API error fire no hook); resident
records (D4). Output per agent: `{state, since, cause}` with `state` one of `working`, `blocked`, `idle`,
`shell`, `starting`, `stopped`, `exited`, `unknown`. Hooks outrank the screen; a newer event outranks an
older one by `firedAtUs` (the rule `accept_hook_event` holds today, moved). The vocabulary is aify-env's;
aify-comms maps it to its own words in one table (`working`→working, `blocked`→blocked,
`idle`→online, `shell`→shell, `starting`→starting, `stopped`/`exited`→offline or stopped by mode,
`unknown`→see D6).

**D3. Hooks report to aify-env's local API.** `POST /agents/:id/state-event` on aify-env, body
`{kind, firedAtUs, harness, pid?, sessionHandle?}`. Endpoint: aify-env injects `AIFY_ENV_URL` into every
process it starts; for a resident, aify-env writes `~/.aify/env.json` `{url, instance, pid}` at boot
(its only writer) and the hook reads it when it fires. A hook is already a separate node process, so
this costs nothing at launch, which keeps the launcher inside hermes' 0.75 s MCP-discovery window. Auth:
the posture of aify-env's other routes (loopback, browser refusal). A local process could forge an event,
the same exposure as today's shared-key hook posts. Per-agent credentials are not added: this is a
local-only install whose key the operator chose to keep simple.

**D4. A resident is a record, not an event.** The launcher writes `~/.aify/residents/<agent>.json`
`{agentId, harness, pid, startedAt, herdrPane?}` at start and removes it at exit; aify-env adopts every
record on boot and on change, and judges the pid itself. So an aify-env restart loses no resident, and a
resident whose launcher died without cleanup is found by its pid, not by a missing heartbeat
(state-based cleanup). Turn state for an adopted resident starts `unknown` until its next hook event.

**D5. The broadcast is pushed and state-based.** A service opts in with its own registry field,
`"agentState": {"path": "/api/v1/hosts/agent-state"}`, never through `advertise` (aify-dashboard sets
`advertise: false` and wants state). Each push carries full records for the agents that changed:
`{machineId, epoch, agents: [{agentId, state, since, cause, seq, harness, mode, pid?, sessionHandle?}]}`.
`epoch` is aify-env's instance boot; `seq` is monotonic per agent within an epoch; a service keeps the
newest `(epoch, seq)` and drops older. A full snapshot goes every 60 s and on attach, so a lost push is
repaired by state, not by replay. Pushed, not pulled: aify-comms runs in a container and aify-env binds
loopback, and aify-env already pushes heartbeats and definitions outward. The shape goes to the
aify-dashboard owner before P2 code.

**D6. Unknown is said, and delivery treats it as today's absence.** A service that has had no snapshot
for three periods (its own clock, as `host_activity` judges freshness) shows the agent `unknown`. Delivery
treats `unknown` as not busy, which is what an agent with no turn record gets today. That is my default;
the alternative (hold delivery while unknown) is one predicate. **For the operator: confirm.**

**D7. aify-comms keeps messaging facts and deletes its turn and process proofs, after a shadow.** P4
ingests the broadcast into `host_agent_state` and records, per agent, every disagreement with today's
`derive()` (count, last examples), shown on a doctor row. P6 switches the status record and the delivery
gate (`_turn_busy_holds_delivery` reads busy = `working` or `blocked`) to the broadcast, and deletes: the
turn-start, turn-end and status-event state writes, `turnBusy` on heartbeats, `agent_turn_state` and
`agent_status_state` as inputs, the console-working lease, host_activity, background-work, and the three
bridge detectors. The messaging overlay stays: a running dispatch, a reply owed, unread counts.

**D8. aify-env owns the lifecycle of defined agents.** `aify-env agents start|stop|restart <id>` (CLI
and HTTP), from the definition. aify-comms' start, stop, restart and cold-start of a **defined** agent
become lifecycle requests, carried like definition requests (comms writes, the plugin claims, aify-env
executes and reports). An **undefined** agent keeps today's spawn path in 0.9; retiring it needs every
managed agent defined first. **For the operator: confirm keeping both paths for one tag.**

**D9. Delete the environment "stop" control.** It has no claimer; aify-env's own stop is the host
action. "Forget" stays (service-local). **For the operator: confirm.**

**D10. One state, forwarded to herdr by aify-env.** aify-env already opens managed panes; a resident
names its pane in its record. aify-env reports state to the pane with the seq herdr wants. The launchers'
direct herdr hook retires in the same phase as the comms hooks.

**D11. Each doctor checks its own tier.** To `aify-env doctor`: env-processes, managed-orphans,
env-code-currency, claude-login, context-window (it reads the screen aify-env already holds). Staying in
`aify-comms doctor`: service, api-exposure, external-keys, client-api-key, bridge-*, agent-identity,
skills-installed, session-handles, spawn-queue, definitions-fresh, usage-openai, and a new
`agent-state-fresh` row. gateway-orphans moves with the hermes host (next tag).

## Phases, each reviewed before the next

| phase | repo | what | proves |
|---|---|---|---|
| P0 | plan | contracts: state model (D2), hook route and env.json (D3), resident record (D4), push shape and registry field (D5), lifecycle request (D8); event shape to the aify-dashboard owner | reviewed before any code |
| P1 | wrapper + comms | ride-along: merge aify-wrapper 764d961 (hermes session MCP servers via `hermes config`), wire comms `install.sh` to `lib/hermes-config-cli.mjs` (`--check` before render, write after) | its own tests; independent of the rest |
| P2 | aify-env | pure state model, hook route, env.json, resident adoption, screen observer into the core, herdr forwarding, the push to opted-in services | every D2 precedence case; adoption across a restart; a lost push repaired by the next snapshot |
| P3 | wrapper | hooks report to aify-env, and keep reporting to comms during the shadow; resident record write and removal | hook to model readback across both repos |
| P4 | comms | ingest route, `host_agent_state`, disagreement ledger and doctor row; `agentState` in its own registry entry | stale epoch and old seq refused; staleness to unknown |
| P5 | operator | install and restart; the shadow runs on the real fleet | every disagreement class explained before P6 |
| P6 | comms + wrapper | switch status and delivery to the broadcast; delete D7's list; drop the comms and herdr hooks | the delivery tests written first against today's gate, red on the deleted inputs, green after |
| P7 | aify-env + comms | lifecycle verbs and requests (D8); delete the environment stop control (D9) | a defined agent's start, stop, restart and cold-start never build a spawn spec in comms |
| P8 | all | doctor moves (D11), docs, 0.9.0 bumps, whole-diff review | every suite in all three repos |

## Not in this tag

The hermes gateway host (start, teardown, kill-prior, ports, resume markers, the delivery loop's process
side): the next tag, because `hermes-delivery-loop.mjs` is one closure that owns both the gateway process
and delivery, and separating it is a redesign. Usage readers. The duplicated console mirror. Per-agent
credentials.

## What would prove this wrong

- A shadow disagreement that neither side's known gaps explain: P6 does not start.
- A status reader needing a fact the push does not carry: before P6, derive the reader set from source
  (every reader of `status`, `statusRaw`, `dispatchState`, `agent_turn_state`, `agent_status_state`) and
  check each against D5's fields.
- A delivery regression (a message steered into a busy turn, or held at idle): the P6 tests go red
  against today's gate on the inputs being deleted before anything is deleted.
- Hook latency: a hook post to loopback must not be slower than today's post to the service; measured in
  P3 on this host, both ways, same run.
- A resident lost across an aify-env restart: P2's adoption test restarts the model with records on disk.
