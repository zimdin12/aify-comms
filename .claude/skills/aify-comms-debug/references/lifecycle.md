# aify-comms troubleshooting: Lifecycle, mode-switch, send-gating & registration

## Contents

- [Lifecycle verbs: what is Spawn / Stop / Restart / Reset / Resume-wake](#lifecycle-verbs-what-is-spawn-stop-restart-reset-resume-wake)
- [resident↔managed switch is now safe (handle carries; pi/opencode managed-only)](#residentmanaged-switch-is-now-safe-handle-carries-piopencode-managed-only)
- [Resident send rejected: `resident bridge heartbeat is gone`](#resident-send-rejected-resident-bridge-heartbeat-is-gone)
- [Send to a managed agent with no live claimer](#send-to-a-managed-agent-with-no-live-claimer)
- [Restarting aify-comms kills all managed sessions (by design — clean slate)](#restarting-aify-comms-kills-all-managed-sessions-by-design-clean-slate)
- [Managed agent finished but its reply never landed](#managed-agent-finished-but-its-reply-never-landed)
- [Send rejected because target has queued work](#send-rejected-because-target-has-queued-work)
- [Send rejected: "no online environment can host" / "cannot start live work now"](#send-rejected-no-online-environment-can-host-cannot-start-live-work-now)
- [Registration refused (409): another live wrapper owns this session](#registration-refused-409-another-live-wrapper-owns-this-session)
- [Superseded or stale bridge: claim blocked](#superseded-or-stale-bridge-claim-blocked)
- [Environment still shows online after bridge stopped](#environment-still-shows-online-after-bridge-stopped)
- [`aify-comms` exits with `environment ... was superseded`](#aify-comms-exits-with-environment-was-superseded)
- [Bridge "lost" the agent / has to be re-registered manually](#bridge-lost-the-agent-has-to-be-re-registered-manually)
- [Re-register seemingly "not taking effect"](#re-register-seemingly-not-taking-effect)
- [Manual mode-switch unavailable in dashboard](#manual-mode-switch-unavailable-in-dashboard)
- [A `stopped` agent still shows "Console attached" (misleading)](#a-stopped-agent-still-shows-console-attached-misleading)

## Lifecycle verbs: what is Spawn / Stop / Restart / Reset / Resume-wake

**Question.** "What's the difference between restart, recover, recreate, resume?" — or
a script calling `recover`/`resume` on `POST /sessions/{id}/control` now 400s.

**Answer (cleaned 2026-06-03, `13d3821`).** The dead `recover` and `resume` actions on
`POST /sessions/{id}/control` were **byte-identical to `restart`** and are GONE — only
`restart`/`recreate`/`stop`/`cli_takeover` remain on that endpoint. Use `restart` instead.
The canonical verbs:

| Verb | Meaning |
|------|---------|
| Spawn | Create a fresh managed backing (no resume). |
| Stop | Halt the running backing; keep spec/handle/identity. Reversible via Restart. |
| Restart | Re-spawn and RESUME native context (`resume_policy=native_first`; carries `session_handle`). |
| Reset (fresh context) | Re-spawn discarding native handle/state (`resume_policy=fresh_context`; was labeled "Recreate"). |
| Resume wake | Re-enable wake/dispatch for a stopped RESIDENT agent — `POST /agents/{id}/control` action=`resume` (no spawn; separate endpoint, deliberately kept). |
| Pause for CLI | Hand session ownership to the terminal; return via Restart. |
| Switch managed/resident | Ownership flip (see next entry). |
| Set handle | Operator repair of the native resume target. |
| Interrupt / Steer | Run-level control. |
| Remove | Tombstone the identity. |
| Rename | Re-key to a new id (`POST /agents/{id}/rename`): preserves `session_handle` + all history, tombstones the OLD id (sends to it are rejected). The live session stays bootstrapped under the OLD id → orphaned until re-registered/relaunched under the new id (the response's `note`/`hadLiveBridge` flag this); tell teammates the new id. |
| Kill bridge / Forget | Environment-level. |

Old dashboards/scripts that said "Recreate" or called the removed `recover`/`resume`
session actions should map to **Reset (fresh context)** or **Restart** respectively.

**Stop/Restart/Reset/cli_takeover now SYNCHRONOUSLY kill the live managed PTY (2026-06-07,
`8ef31a2`).** A session-control action enqueues a terminal stop, and `TERMINAL_MANAGER.stop`
escalates SIGTERM→SIGKILL on the managed PTY in-band, so the live worker is gone by the time
the action returns. This closes the old gap where an operator Stop/Restart left a headless
orphan (live PTY, no claimer) hanging until the 60s `_reconcile_managed_worker_hygiene`
hygiene reaper swept it. The 60s reaper is now the BACKSTOP for crash/leak residue only — a
clean lifecycle action no longer relies on it. (Restart/Reset then re-spawn fresh per the
verbs above; the kill is the teardown half of the same action.)

## resident↔managed switch is now safe (handle carries; pi/opencode managed-only)

**Symptom (old footgun).** Switching a pi/opencode agent to resident left it
`presence-only` and every dispatch rejected; or switching a codex/hermes/claude agent
resident→managed lost its native chat memory (the worker started a fresh thread/transcript).

**Fix (2026-06-03, `13d3821`).** Pull/rebuild + restart the service.
- **Full-duplex (both modes):** claude-code, codex, hermes. **Managed-only** (resident is
  presence/debug metadata, NOT live-wakeable): pi, opencode. `managed→resident` is now
  **rejected** for pi/opencode (actionable 409; the dashboard hides their "Switch to
  resident" button) instead of silently creating an undeliverable presence-only agent.
- **`resident→managed` now carries the native `session_handle`** into the managed-warm
  coldstart spawn, so the managed worker RESUMES the same codex thread / hermes gateway /
  claude transcript instead of starting fresh. (Per-agent chat carries over regardless — it's
  keyed per agent, not per session.)
- An **advisory warning** fires when you bind a native handle another LIVE agent already owns
  (e.g. two agents sharing one codex thread); the dashboard offers a confirm→`force` retry on
  the active-run 409.

## Resident send rejected: `resident bridge heartbeat is gone`

**Symptom.** `comms_agent_info` or the dashboard shows a resident Hermes,
Claude, or Codex agent with live-looking metadata (`wakeMode: hermes-live`,
`claude-live`, or `codex-live`), but sending returns `ok: false`,
`recipientStatus: offline`, and a reason like `resident bridge heartbeat is
gone; restart the resident wrapper or switch to managed`. In Hermes, the open
terminal does not receive the prompt.

**Cause.** The agent record was updated without a current wrapper bridge. The
common bad workaround is a raw Node/curl `POST /api/v1/agents` that passes
`runtimeConfig.gatewayUrl` or a session handle. That writes metadata, but it
does not start the MCP stdio bridge inside the visible `*-aify` wrapper, does
not create `runtimeState.bridgeInstanceId`, and does not heartbeat
`bridge_instances`. With no live bridge heartbeat the agent reads `offline`
(the proof-based engine no longer has a separate `stale` state), so the server
refuses live delivery instead of forking hidden work.

**Fix.** Restart the exact visible wrapper that should own delivery, then
register from inside that same session with the MCP tool:

```
mcp_aify_comms_comms_register(agentId="target", role="tester", runtime="hermes")
mcp_aify_comms_comms_agent_info(agentId="target")
```

For Hermes, use the prefixed callable names that Hermes assigns to MCP tools (`mcp_aify_comms_comms_register`, `mcp_aify_comms_comms_agent_info`, `mcp_aify_comms_comms_send`). Missing unprefixed names like `comms_register` is not an exposure failure if the prefixed tools are available. For Claude/Codex use the matching runtime and handle fields documented in the main skill. Prefer launching with `--aify-agent <id>` so the wrapper's MCP child auto-registers with its real bridge id. Do not repair this by posting to `/api/v1/agents` manually; use dashboard **Switch to managed** if the open resident terminal should not own delivery.

## Send to a managed agent with no live claimer

**Current behavior.** Sends are live-delivery gated. An `available` managed agent with
an online capable environment is cold-started and gets a claimable queued run. A busy
live agent receives steering when supported or queued/merged next-turn work when not.
An `offline`, `stopped`, or otherwise non-startable target fails visibly and the
message is not stored for a future surprise. The queued-run backstop
(`queued_run_backstop_seconds`, default ~180s) remains a crash/leak safety net for a
run that was validly created but later lost its claimer; it is not a substitute for
the send gate.

**If you expected immediate delivery and it queued instead.** Confirm whether the
target is `working` (next-turn queue is expected) or its cold-start spawn is still
being claimed. If neither is true, inspect the environment bridge and backing from
Sessions/Console rather than creating another message.

## Restarting aify-comms kills all managed sessions (by design — clean slate)

**Symptom / question.** After restarting the `aify-comms` environment bridge, every
managed agent's Console is gone and its worker processes (gateway hosts, delivery
loops, daemons, PTYs) are no longer running. Agents read `offline` while the owning
environment bridge is down. After the bridge re-registers, managed identities with
a usable spec read `available` until lazy-started by a send (or eagerly respawned by
the configured spawn loop).

**This is intended (2026-06-02).** Restarting `aify-comms` is a guaranteed **clean
slate** for managed sessions, so a restart can never leave dead claimers holding busy
agents, orphaned gateway hosts, or `hermes.exe` proliferation — even after a hard
crash. Two hooks enforce it:

- **Shutdown teardown** — on graceful shutdown (and the supersede path), the bridge
  tears down every managed session it owns: stops console PTYs, port-kills gateway
  hosts, reaps detached delivery loops/daemons.
- **Boot survivor sweep** — on the next start, before the spawn loop, it reaps any
  managed-triad survivors of a crashed/SIGKILL'd predecessor whose owning bridge is
  no longer live in `bridge_instances`. Replacement startup is serialized as
  register replacement → sweep predecessor survivors → adopt managed ownership →
  start spawning. Do not sync/adopt before the sweep: that can make the old bridge
  target nothing while the new bridge mistakes the predecessor's processes for its
  own fresh workers.

Both are **scoped to the agents this env bridge owns** (its `cwdRoots`) and **never
touch resident sessions or another env's agents**. For graceful shutdown, “owns”
must come from a fresh service snapshot, not cached `REMOTE_AGENT_STATE`: the cache
can retain a stale managed row after managed→resident takeover, while resident and
managed Hermes delivery loops have the same command shape. If ownership cannot be
read because the service is already offline, shutdown reaps nothing; the next boot
sweep is the safe backstop. If registration or the ownership sweep is unavailable,
the replacement does not adopt managed ownership or start spawning; it retries from
the heartbeat loop. Managed sessions are re-spawned fresh from their spec by
the dashboard/spawn loop — they are not inherited across a restart. If you need a
session to persist a restart with its terminal intact, run it **resident**
(`*-aify`), which teardown explicitly excludes.

## Managed agent finished but its reply never landed

**Symptom.** A managed claude/hermes agent clearly handled a `require_reply` dispatch
(you can see the work in its Console), the run sits `delivered`/`awaiting reply`, and
the reply only shows up much later (~20min) when the next dispatch happens to wake the
agent again — or never.

**Cause (root-caused 2026-06-02).** The agent **read the message in one turn and
deferred its `comms_send(inReplyTo=...)` reply to a later turn.** A managed/channel
session goes idle after a turn ends and is **NOT re-woken to finish a deferred
reply** — so the reply strands until some unrelated dispatch re-wakes the session.
This is not a threading/infra defect; the reply threads correctly once it is actually
sent.

**Fix.** Reply in the SAME turn. The channel wake text for `require_reply` dispatches
now explicitly instructs the agent to call `comms_send(inReplyTo="<id>")` in THIS turn
before ending, and warns that a managed session will not be re-woken to finish a
deferred reply (relaunch the wrapper to load the updated `claude-channel.js`). If you
are the agent: do not split read and reply across turns — send your reply before you
end the turn. The WS3 queued-run backstop will eventually fail a truly undeliverable
run, but the correct behavior is the same-turn reply.

## Send rejected because target has queued work

**Symptom.** `comms_send` returns `ok: false` with `reason: "agent already has queued work"` or `reason: "agent is working"`.

**Cause.** Normal send is live-delivery gated for unreachable agents. Current builds still allow busy live agents: steer-capable targets receive a steer control, and busy non-steer targets queue/merge as next-turn work. Seeing this error usually means the target is not actually live-startable, the bridge is stale, the target is paused/stopped/offline, or the queue is blocked by stale state.

**Fix.** Pick one of:
- Wait for the in-flight/queued run to finish.
- `comms_run_interrupt(runId=<current active run>)` if the current work should stop.
- Use the dashboard run controls to cancel stale queued work.
- `comms_agent_info(agentId=<target>)` to inspect why the agent is not currently startable.
- If the agent is actively running, ordinary `comms_send(...)` should steer when supported or queue/merge as the next-turn fallback. Set `steer=true` only when you need to be explicit; use `queueIfBusy=true` only to force next-turn delivery. When `queueIfBusy=true`, any `steer` value is intentionally ignored.

## Send rejected: "no online environment can host" / "cannot start live work now"

**Symptom.** `comms_send(trigger=true)` to an agent that looks `available` returns `ok:false`. Newer builds say "No online environment can host managed `<runtime>`…".

**Cause.** An `available` managed agent now AUTO-STARTS on send (the service cold-starts a bridge-claimed worker, auto-binding the freshest online env that advertises the runtime). It only rejects when (a) no online environment advertises that runtime, or (b) the agent is explicitly **disabled** (operator Stop → `status:'stopped'`, `wakeMode:'disabled'`) — a disabled agent never auto-starts and refuses other agents' sends.

**Fix.**
- Start/restore an environment bridge that advertises the runtime (`comms_envs()` to see what each env offers), then retry.
- If the target is `stopped`/disabled, use **Restart** for a managed session or **Resume wake** for a resident agent.
- `comms_agent_info(agentId=<target>)` to confirm the runtime, env binding, and `wakeMode`.

## Registration refused (409): another live wrapper owns this session

**Symptom.** A wrapper auto-register / `comms_register` from a restarted resident session is refused with `409` "agent X already has a LIVE resident bridge (seen Ns ago) … pass force=true (AIFY_FORCE_REGISTER=1)".

**Cause.** Phase 4 same-mode race guard: a DIFFERENT bridge is registering a resident identity whose prior same-mode bridge is still heartbeating. The service refuses to silently supersede a live wrapper (which would kill its in-flight work). Heartbeats are 60s-grained, so a just-killed wrapper can still look "live" for up to the resident lease (~150s).

**Fix.**
- If a second wrapper is genuinely running for the same identity, stop one — they were racing.
- If YOU restarted the prior wrapper and want the new one to take over, relaunch with `AIFY_FORCE_REGISTER=1` (or wait out the lease window). Managed agents are unaffected (latest-launch-wins); the visible-TUI delivery-owner registrations are also exempt.

## Superseded or stale bridge: claim blocked

**Symptom.** A bridge's dispatch loop logs `blockedBy: {reason: "bridge_superseded"}` or `blockedBy: {reason: "bridge_not_current"}`.

**Cause.** A newer `comms_register` for the same `agentId` on the same machine has replaced this bridge. For Codex/OpenCode/Pi, the server also compares the polling bridge ID against the agent's current `runtimeState.bridgeInstanceId`; this catches old processes whose bridge row is gone but whose dispatch loop is still alive.

**Fix.** Shut the superseded bridge down. This is not an error — it's the server protecting the queue. The fresh bridge is the one that should be claiming runs.

## Environment still shows online after bridge stopped

**Symptom.** The Environments page still shows a Windows/WSL/Linux bridge as `online` after you closed the visible terminal, or the same environment cards keep changing order.

**Cause.** Environment presence is heartbeat-based. A graceful `Ctrl+C` on current bridge code sends one final offline heartbeat. A hard kill, crashed terminal, machine sleep, or older bridge build can only be inferred after missed heartbeats. If `lastSeen` is still updating, some process is still posting as that bridge; the dashboard card shows the bridge process PID from heartbeat metadata when available.

**Fix.**
- Pull latest, reinstall, and restart `aify-comms` so the bridge has graceful offline reporting.
- Check for leftover processes with `ps -ef | rg 'aify-comms|mcp/stdio/server.js'` on WSL/Linux, or `Get-Process node | Select-Object Id,Path,CommandLine` on Windows.
- Starting a newer `aify-comms` for the same environment supersedes older bridge heartbeats when both advertise `bridgeStartedAt`; the server also queues a stop control for the older bridge. A fresh bridge ignores stale stop controls that were requested before that bridge started. Old OS processes still need manual cleanup if they are hung and no longer polling, but they should not own spawn claims.
- Use the dashboard **Kill bridge** action while the bridge is online. Managed agents from that environment become offline/detached; chats and identities remain. Assign them to another online environment from **Sessions -> Identity Directory** or restart the bridge, then restart from **Sessions**.
- Use **Forget** only to hide an obsolete execution target. Forgetting keeps agent identities, chats, saved spawn specs, and session records; it no longer deletes managed identities.
- If a spawn request is marked `running` but the first brief dispatch failed, current server code repairs it to `failed` on the next spawn-request list refresh.

## `aify-comms` exits with `environment ... was superseded`

**Symptom.** A bridge terminal exits shortly after start with a message like `environment windows:host:default was superseded by replacement bridge ..., pid ..., cwd ...`.

**Cause.** Only one bridge is current for a given environment ID such as `windows:HOST:default` or `wsl:HOST:default`. A newer bridge heartbeat for the same environment replaced this process, so the server sent this older bridge a targeted stop control. This is intentional: old bridges must not keep claiming spawns or managed runs after a newer bridge takes ownership.

**Fix.** Keep one `aify-comms` process per environment. If the replacement cwd/pid is not the one you want, stop that replacement process from the Dashboard **Environments -> Kill bridge** action or with the OS process manager, then start `aify-comms` from the directory/root you want to be current. The terminal message names the replacement bridge, PID, and cwd so you can identify it.

If the replacement cwd is an agent workspace and appears immediately after a managed runtime run starts, the bridge is running an old launcher/runtime that lets child MCP servers inherit `AIFY_ENVIRONMENT_BRIDGE=1`. Pull latest, rerun the installer, and restart the OS bridge. Current launchers mark the real bridge with `--environment-bridge`, and managed child processes strip bridge-only env vars before spawning.

## Bridge "lost" the agent / has to be re-registered manually

**Symptom.** An agent that used to work stops claiming dispatches. Messages still arrive in its inbox but nothing launches. Manually re-registering the agent makes it work again.

**Cause.** On older builds, the server could not distinguish "agent was intentionally removed" from "agent disappeared because the DB was rotated or cleared accidentally." The bridge's local cache kept polling with the old `agentId`, saw `404`, and auto-re-registered it. Current builds use intentional-remove tombstones: dashboard DELETE / `comms_remove_agent` / `comms_clear(target="agents", agentId=...)` return `410 Gone` to that bridge cache, so the bridge forgets the ID instead of recreating it. Plain `404` still means "server forgot this agent" and may auto-re-register.

**Auto-recovery (current build).** The bridge now:
- Retries transient HTTP errors up to 3 times with exponential backoff (250ms / 500ms / 1000ms) before giving up on any single call.
- Watches for `404` responses on `/agents/{id}` and `/dispatch/claim`. A 404 means the agent is unknown to the server, so the bridge automatically re-registers from its cached agent data.
- Watches for `410` responses on `/agents/{id}` and `/dispatch/claim`. A 410 means the ID was intentionally removed, so the bridge stops tracking it.
- Counts consecutive claim failures per agent. After 4 in a row, the bridge tries an auto-re-register from cache as a last-resort self-heal.

Look for these lines on stderr:

```
[aify] agent "foo" missing from server; auto-re-registering
[aify] auto-re-registered "foo" from cached state
[aify] stopped tracking "foo": server marked it intentionally removed
[aify] 4 consecutive dispatch/claim failures for "foo"; attempting auto-re-register
```

**Fix when auto-recovery fails.** If you see the auto-re-register log followed by `auto-re-register failed for "foo"`, the server itself is unreachable or rejecting the payload. Check:
1. `curl http://localhost:8800/health` — is the server even up?
2. The bridge's cached state may be missing a required field (role, runtime) if the agent was never fully registered in the first place. Manual `comms_register(...)` with complete fields is the definitive recovery.

**`terminal control claim failed: transient HTTP error against http://localhost:8800: fetch failed`.** One or two of these during `docker compose up -d --build`, bridge restart, or service restart are expected: the bridge poll hit the API while the TCP connection was being reset, and current code retries on the next poll. If it repeats while `/health` is healthy, the bridge is probably still using an old `localhost`-only URL from another shell/checkout. Restart the host bridge from the latest checkout and prefer `http://192.168.100.10:8800` for Windows/WSL installs; current bridge builds also try fallback URLs for stale `localhost` configs.

**Removing one bad ID.** Use:

```
comms_remove_agent(agentId="wrong-id")
```

or:

```
comms_clear(target="agents", agentId="wrong-id")
```

Do not use `comms_clear(target="agents")` unless you intend to remove every agent.

## Re-register seemingly "not taking effect"

**Symptom.** You re-register with new values but `comms_agent_info` still reflects the old ones.

**Cause.** Re-register is a **full state refresh** for session-related fields. If you pass `sessionHandle=""` (empty) or omit it, that's what gets stored — old session handles are cleared. If the result is "wrong", the bridge did what you asked.

Note that `description` is the one exception: omitting it preserves the existing value. Pass `description=""` to clear it explicitly.

**Fix.** Pass every field you care about on the re-register call. For Codex resident triggering, that usually means `cwd`, `sessionHandle`, and `appServerUrl` all explicit. If you only need to repair a known saved native ID, prefer Dashboard **Set handle** so you do not accidentally refresh unrelated identity fields.

## Manual mode-switch unavailable in dashboard

**Symptom.** Operator wants to flip an agent from `resident` to `managed` (or back) without killing the wrapper, but no switch button is visible in the dashboard's Details panel or Sessions rail. Chip-style "Switch to managed" / "Switch to resident" controls described in Plan 6 C are documented but don't render on screen.

**Current behavior.** The switch is no longer gated by `manual_session_mode`. It should be visible for agents whose `sessionMode` is `resident` or `managed` in Chat details and Sessions actions.

**Detection.** Confirm the agent exposes a switchable mode:

```bash
curl -s http://localhost:8800/api/v1/agents/AGENT_ID | python -m json.tool | grep sessionMode
```

If the value is not `resident` or `managed`, no switch is rendered. If it is switchable and still missing, the dashboard assets are stale or the browser has cached old JS.

**Fix.** Rebuild/redeploy the service (`docker compose up -d --build`) and hard-refresh the browser. Clicking the switch calls `PATCH /api/v1/agents/{id}/session-mode {mode}`; the response updates `sessionMode`, launch mode, capabilities, runtime state, and any side-effect terminal state.

## A `stopped` agent still shows "Console attached" (misleading)

**Symptom.** An agent reads `stopped` (or a dead session) but the console pill says "Console
attached" — it looks like it has a live console while stopped.

**Cause + fix (2026-06-05).** Two parts. (1) The usual trigger was the hermes resume-error strand
above (dead `--resume` → `session not found` → the PTY lingered attached while the session was torn
down) — removed at the root by `5c1617a` (DB-validated resume → clean fresh start). (2) The legacy
dashboard label keyed purely on the terminal row's `attached` status, so a brief teardown race
showed "Console attached" under a dead session. `b69bda3` makes the label honest: a session in a
dead state (`stopped`/`failed`/`ended`/`lost`/`cancelled`) renders its real state ("Console
stopped" / "Console failed"), never "attached". NOTE: a managed agent whose last session FAILED
correctly stays **`available`** (not `blocked`/`errored`) — it lazy-respawns on the next send, so
it is genuinely available-to-retry (a `failed → blocked` status change was tried and rejected; see
DECISIONS.md 2026-06-05). Requires the container rebuild to deploy the dashboard.
