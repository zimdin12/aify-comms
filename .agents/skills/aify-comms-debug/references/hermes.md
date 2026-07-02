# aify-comms troubleshooting: Hermes (gateway, sessions, TUI, delivery)

## Contents

- [Resident Hermes wakes, but dashboard shows no session evidence](#resident-hermes-wakes-but-dashboard-shows-no-session-evidence)
- [Managed Hermes dashboard send fails with `visible session not found`](#managed-hermes-dashboard-send-fails-with-visible-session-not-found)
- [Resident Hermes send says managed wrapper PTY is unavailable](#resident-hermes-send-says-managed-wrapper-pty-is-unavailable)
- [Resident Hermes reports live but send fails with `ECONNREFUSED 127.0.0.1:<port>`](#resident-hermes-reports-live-but-send-fails-with-econnrefused-127001port)
- [Hermes `gateway websocket connection failed` / two agents collide on one port](#hermes-gateway-websocket-connection-failed-two-agents-collide-on-one-port)
- [Managed hermes TUI shows, then drops with `gateway websocket connection failed`](#managed-hermes-tui-shows-then-drops-with-gateway-websocket-connection-failed)
- [Hermes `mcp test` works, but live turn has no aify tools](#hermes-mcp-test-works-but-live-turn-has-no-aify-tools)
- [Hermes fails immediately with `'NoneType' object is not iterable`](#hermes-fails-immediately-with-nonetype-object-is-not-iterable)
- [Many `hermes.exe` processes for a few hermes agents](#many-hermesexe-processes-for-a-few-hermes-agents)
- [Managed hermes never shows `working` during a turn](#managed-hermes-never-shows-working-during-a-turn)
- [Hermes agent shows `online` while working](#hermes-agent-shows-online-while-working)
- [EVERY managed-hermes dispatch fails "Queued >180s … up-but-deaf" (gateway host died — hermes 0.15.1 `--tui`)](#every-managed-hermes-dispatch-fails-queued-180s-up-but-deaf-gateway-host-died-hermes-0151---tui)
- [Hermes native fallback ACP persistent session](#hermes-native-fallback-acp-persistent-session)
- [Hermes-aify wrapper fell through to plain hermes (Plan 5 Section A)](#hermes-aify-wrapper-fell-through-to-plain-hermes-plan-5-section-a)
- [Hermes dispatch completes but open console does not move](#hermes-dispatch-completes-but-open-console-does-not-move)
- [Hermes inter-agent delivery: queued forever / fabricated "delegated" reply / never renders in TUI / splits sessions (2026-06-03)](#hermes-inter-agent-delivery-queued-forever-fabricated-delegated-reply-never-renders-in-tui-splits-sessions-2026-06-03)
- [Hermes starts a FRESH session after an aify-comms restart](#hermes-starts-a-fresh-session-after-an-aify-comms-restart)

## Resident Hermes wakes, but dashboard shows no session evidence

**Symptom.** Two resident Hermes terminals communicate successfully and show
boxed `aify-comms message` notice in the native TUI, but the dashboard Sessions or
Chat details view has no concrete resident session/console evidence for that
agent.

**Cause.** Older service builds treated resident registration as identity-only:
they updated `agents.runtimeConfig` and bridge heartbeat state, but did not
create a dashboard-visible `agent_sessions` row. Delivery still worked through
the wrapper bridge, while the dashboard had no session object to anchor.

**Fix.** Rebuild/restart the service with the current `api_v2.py`, then
restart and re-register the resident `hermes-aify` terminal. Current resident
registration upserts an `agent_sessions` row with `mode=resident`,
`owner_mode=resident`, the current environment id, session handle, gateway
metadata, and owner bridge id. Verify with `GET /api/v1/sessions?agentId=...`
or the Sessions page.

## Managed Hermes dashboard send fails with `visible session not found`

**Symptom.** A dashboard send to a managed Hermes agent fails quickly with:

```
Hermes visible-session binding failed for <old-id>: visible session not found
```

Events show `channel / start_if_possible`, the run was claimed, and no compact
boxed `aify-comms message` notice appears in the target
`hermes-aify` Console.

**Cause.** The run was queued correctly as channel-mode wrapper-backed work,
but either an old bridge build let the environment bridge claim it before the
`hermes-aify` wrapper child bridge, or the wrapper child claimed while its
Console was still stuck at `resuming...` for a stale saved handle. The
environment bridge only has stored `runtimeConfig.gatewayUrl` / `sessionHandle`,
and a not-ready wrapper has no bindable active visible session, so stale
records can point at an old gateway/session and fail visible-session binding.

**Fix.** Rebuild/restart the service and restart the host `aify-comms` bridge
so the environment bridge no longer advertises channel claim modes for
wrapper-backed Codex/Hermes. The service also rejects such claims unless the
claimant bridge is registered as `bridge_kind='managed-wrapper-child'`, and it
blocks Hermes wrapper-child claims while the active Console still shows
`resuming...` rather than `ready`. Current terminal managers heal a long-stuck
Hermes resume by restarting once without `--resume`. Then restart/recover the
managed Hermes session or send again; the wrapper PTY child bridge should claim
after readiness and the visible Console should render the compact wake notice.
If the saved Hermes session key is stale but the wrapper gateway has a current
visible session, current bridges retry visible binding against that active
session and emit `visible session bind retry: <old> -> <current>` instead of
creating or resuming a hidden session.

## Resident Hermes send says managed wrapper PTY is unavailable

**Symptom.** `comms_agent_info` reports a resident Hermes agent as
`Wake mode: hermes-live`, but `comms_send(...)` fails before
sending with:

```
Managed hermes wrapper PTY is unavailable; recover or restart the environment-managed session.
```

**Cause.** This is a service routing bug, not a Hermes registration problem:
the `/messages/send` preflight selected resident delivery, but a later generic
native-managed branch ignored the selected execution mode and tried to require
a managed wrapper PTY for every Hermes runtime.

**Fix.** Rebuild/restart the service with the current `api_v2.py`. Resident
Hermes sends should persist `executionMode=resident` and claim through the
current resident bridge. Verify with `comms_run_status(runId=...)`; events
should show `visible session bound`, `prompt.submit`, and `turn completed`.

## Resident Hermes reports live but send fails with `ECONNREFUSED 127.0.0.1:<port>`

**Symptom.** `comms_agent_info` says `Wake mode: hermes-live`, but
`comms_send(...)` fails with:

```
Hermes gateway WS open failed: connect ECONNREFUSED 127.0.0.1:<port>
```

The stored `runtimeConfig.gatewayUrl` points at an older
`hermes-aify-dashboard-<port>` while newer `hermes-aify` dashboard ports are
listening.

**Cause.** A resumed Hermes process can inherit an old
`AIFY_HERMES_GATEWAY_URL` from its parent shell. Older wrappers/plugins
preserved that value, so the MCP child registered a fresh bridge heartbeat
against a dead dashboard gateway. The bridge looked live because the MCP
stdio process was still heartbeating, but the actual Hermes TUI gateway was
not reachable.

**Fix.** Update/redeploy aify-comms, rerun `install.sh --client hermes`, and
restart each `hermes-aify` terminal before re-registering. Current wrappers
clear inherited gateway env before starting the dashboard, and the Hermes
plugin always overwrites MCP-child env with the gateway URL owned by the
current dashboard process.

## Hermes `gateway websocket connection failed` / two agents collide on one port

**Symptom.** A managed/resident hermes agent fails to come up with a "gateway
websocket connection failed" error, typically when two hermes agents on the
same host hash to the same base gateway port (the observed
comms-senior-dev/graph-hermes-tl collision on `9341`).

**Cause.** `resolveGatewayPort` picked a port from a deterministic hash of the
agent id and only checked that the port was bindable — it did not check whether
another agent had already taken it. Two agents hashing to the same base port
raced for the one port; the loser's gateway WS never came up.

**Fix (2026-06-01).** `resolveGatewayPort` now assigns a port that is both
bindable AND cross-agent unique — it reads the other agents'
`aify-hermes-port-*` marker files and skips ports already claimed. Two
colliding agents now get distinct ports automatically. Takes effect when a
hermes agent respawns (relaunch its `hermes-aify`). The old deterministic-pin
workaround (manually pinning one agent to a free port) is no longer needed.

## Managed hermes TUI shows, then drops with `gateway websocket connection failed`

**Symptom.** A managed hermes agent's visible TUI launches and renders fine in the
dashboard Console, but moments later (often after a transient hiccup) the TUI's
WebSocket drops with `gateway websocket connection failed` and the console goes dead,
even though no port collision is involved (distinct from the collision entry above).

**Cause.** An old bridge build's **delivery loop port-killed the SHARED gateway
host**. In the managed flow the wrapper's ensure-host spawns the gateway BEFORE the
loop, so the loop REUSES it; but the loop's teardown had an `else if (gatewayPort)
killByPort` branch that port-killed that gateway whenever the loop exited (e.g. a
transient 410). Since the visible TUI shares the same gateway, killing it dropped the
TUI's WebSocket.

**Fix (2026-06-02, `774fb07` + `14cf5ed`).** Pull and relaunch the agent's
`hermes-aify`. The loop now kills the gateway **only if it spawned that host itself**
(an owned child handle) and **never port-kills a reused/shared gateway**; it also no
longer clears the gateway port/key markers (kill-prior needs the persisted port
marker to reap the gateway on relaunch). A SECOND root cause of the same symptom was
kill-prior itself: the managed wrapper calls it twice (pre-spawn + a post-spawn
self-reap-race call), and the post-spawn call ran AFTER ensure-host started the
CURRENT gateway on the agent's port, so its port-kill killed the live gateway the TUI
was about to attach to. The gateway port-kill, daemon stop, AND resume-TUI reap are
now gated **pre-spawn only** (`14cf5ed` + `99563af`); the post-spawn call reaps only
stale delivery loops. The gateway's lifetime ties to the TUI/console — reaped by
kill-prior on relaunch and the env-bridge survivor sweep on restart, not by a loop
exit. A wrapper still on old code is the one to relaunch; verify the TUI's WebSocket
now survives a loop restart.

## Hermes `mcp test` works, but live turn has no aify tools

**Symptom.** Inside `hermes-aify`, `hermes mcp list` shows `aify-comms`
enabled and `hermes mcp test aify-comms` discovers `comms_register`,
`comms_send`, and `comms_agent_info`, but the model turn still cannot call
`mcp_aify_comms_comms_register`, `mcp_aify_comms_comms_send`, or
`mcp_aify_comms_comms_agent_info`.

**Cause.** `hermes mcp test` is a fresh CLI process. The visible
`hermes-aify` terminal is driven by a separate `hermes dashboard`
gateway process (pre-0.15.1 this was `hermes dashboard --tui`; `--tui`
moved to a top-level flag in 0.15.1 and the `dashboard` subcommand now
rejects it — see the gateway-host entry below), and older wrapper/plugin builds did not run
`discover_mcp_tools()` before that gateway built the TUI `AIAgent`. The gateway
could therefore have only built-in tools even though the standalone MCP test
passed.

**Fix.** Update aify-comms, run `./install.sh --client hermes`, and restart
`hermes-aify` so it loads the current `integrations/hermes-aify-plugin` shim.
The plugin now runs MCP discovery before the TUI agent is built. For an
already-open session on a current wrapper, reload the gateway MCP registry
instead of using terminal/Node/curl/direct HTTP registration. Direct HTTP
registration can still corrupt bridge-backed resident metadata.

If the prefixed tools are exposed and `mcp_aify_comms_comms_register` succeeds
but `comms_agent_info` still reports `Wake mode: hermes-missing-handle`, the
dashboard-gateway MCP child probably registered without
`runtimeConfig.gatewayUrl`. Update/redeploy again and restart `hermes-aify`:
current wrappers export `AIFY_HERMES_PORT` before the dashboard starts, and the
Hermes plugin injects `AIFY_HERMES_GATEWAY_URL` inside
`hermes_cli.web_server` so dashboard-side MCP calls can self-register live.

## Hermes fails immediately with `'NoneType' object is not iterable`

**Symptom.** A freshly restarted `hermes-aify` terminal reaches the Hermes TUI,
but an ordinary prompt such as "read aify-comms skills again and re-register"
fails before tools run:

```
API call failed: TypeError
Provider: openai-codex
Error: 'NoneType' object is not iterable
```

The wrapper may already be healthy: process list shows
`hermes.exe --tui --resume <id>` and
`~/.local/state/aify-comms/hermes-aify-active-session-<port>.json` exists.
On native Windows, current installs run `hermes-aify.cmd` through a generated
PowerShell shim, not Git Bash, so Hermes' Node TUI keeps a real console TTY.
If `hermes-aify --resume <id>` exits with `hermes-tui: no TTY`, redeploy the
Hermes wrapper and verify `C:\Users\Administrator\.local\bin\hermes-aify.cmd`
calls `hermes-aify.ps1`.

**Cause.** This is a Hermes/OpenAI SDK Responses streaming edge case, not an
aify registration error. ChatGPT Codex can stream valid `response.output_item.done`
function-call items and then finish with `response.completed.response.output`
set to `null`; OpenAI SDK 2.24.0 raises this local `TypeError` before Hermes
gets to call MCP tools. Current `install.sh --client hermes` installs the
`hermes-aify` runtime shim (`integrations/hermes-aify-plugin`) and the wrapper
loads it with `AIFY_HERMES_PLUGIN=1` / `PYTHONPATH`. The shim handles this
exact SDK failure in memory: it falls back to Hermes's lower-level
`create(stream=True)` path and rebuilds `response.output` from the
already-streamed items.

**Fix.** Pull/update aify-comms, run the Hermes client install/redeploy, then
restart the affected `hermes-aify` terminals so the Python process imports the
shim. If the same error repeats after restart, check the active
dashboard log at:

```
~/.local/state/aify-comms/hermes-aify-dashboard-<port>.log
```

and verify the wrapper loads the plugin:

```
head -80 ~/.local/bin/hermes-aify | grep AIFY_HERMES_PLUGIN
```

For A/B testing upstream Hermes without the shim, launch with
`AIFY_HERMES_DISABLE_PLUGIN=1 hermes-aify`. The old in-place source patch path
is legacy/debug only: set `AIFY_HERMES_LEGACY_SOURCE_PATCH=1` before
`install.sh --client hermes`.

## Many `hermes.exe` processes for a few hermes agents

**Symptom.** `ps` / Task Manager shows far more `hermes.exe` (gateway/api_server)
processes than you have hermes agents — e.g. 13 `hermes.exe` for 4 agents. A
`Another hermes.exe is running` warning may appear on (re)spawn.

**Cause.** Each hermes agent runs a per-agent gateway/api_server daemon
(`hermes-daemon.js`). Older builds spawned a fresh daemon on every
spawn/restart without killing the prior one, so daemons accumulated across
session restarts and wrapper churn.

**Fix (2026-06-02, `8fd3da9`).** `ensureDaemon` now tracks each agent's daemon
PID in `aify-hermes-daemon-pid-<agent>` and kills the prior live daemon before
spawning a replacement; `stopDaemon` kills by BOTH port and tracked PID — one
daemon per agent from then on. Takes effect when the agent's `hermes-aify`
relaunches.

**Relaunch also reaps the prior visible resume-TUI (2026-06-02, `99563af`).**
kill-prior used to reap the prior delivery loop, gateway host, and daemon but NOT
the prior `hermes --tui --resume <real-session-id>` visible TUI, so each silent
relaunch leaked a duplicate resume-TUI. kill-prior now reaps that prior resume-TUI
too, matched to the agent's stored native session id (from the
`aify-hermes-session-<agentId>` marker), never a broad `hermes --tui`, gated
**pre-spawn only** so the post-spawn self-reap-race call can't kill the
gateway/daemon/TUI the current launch just started.

**STOP reaps the whole triad; restart reaps the pile (2026-06-02, `f0bdaef`).** You
no longer hand-kill stray `hermes.exe`. A dashboard **Stop** on a managed-hermes
agent now reaps the entire triad (gateway host + delivery loop + daemon),
agent-scoped — not just the console PTY (a resident/claude/other-runtime stop is
never touched). The environment bridge also owns the triad and tears it down on
shutdown; on the next boot it sweeps for survivors of a crashed/killed predecessor
(plus a tombstoned-marker sweep that deletes `aify-hermes-{port,daemon-pid,key}-<agent>`
for agents absent from the live `/agents` keyset) and reaps any whose owning bridge is
no longer live. All scoped to the agents this env bridge owns; resident/other-env
sessions are never touched. So **restarting `aify-comms` collapses the pile to zero
managed survivors** — see "Restarting aify-comms kills all managed sessions (by
design)" below. The daemon kill-prior above is the per-spawn backstop.

**Caveat — REMOVE is not reaped synchronously.** Relaunch and STOP reap the triad
**instantly**. Dashboard **REMOVE** does not: deleting the agent FK-cascades and wipes
the emitted triad-reap stop control before the bridge claims it, so a removed
managed-hermes agent's procs may linger until the **next env-bridge boot** (the
tombstoned-marker + survivor sweeps clean them then). To clean up sooner, restart
`aify-comms`, or stop the affected `hermes-aify` wrappers and kill stray `hermes.exe`
whose port files (`aify-hermes-port-*`) no longer match a live agent, then relaunch.

## Managed hermes never shows `working` during a turn

**Symptom.** A managed hermes (visible-TUI) agent runs a turn but the dashboard
never shows `working` — it stays `online`/`online · awaiting reply`.

**Cause.** `hermes-managed-host.js` delivers via `prompt.submit`, which is
FIRE-AND-FORGET (resolves on accept, not turn completion). The old code pulsed
`turn_busy=true` then cleared it in a `finally` immediately after submit — so
working flipped 1→0 while the turn was only just starting. (The blocking
`hermes-channel.js` path is fine — its `chatStream` runs the turn to completion
before clearing.)

**Fix (2026-05-31, refined 2026-06-02 `2216c44`).** On a successful submit the loop
leaves `turn_busy` set rather than clearing it in a `finally`, so `working` reflects the
real turn. As of 2026-06-02 turn-state is driven by a **continuous, bidirectional
gateway-status detector** (`mcp/stdio/hermes-gateway-turn-detector.js` in `runDeliveryLoop`):
gateway session `working` → `/turn-start`, gateway session `idle` SUSTAINED (≥3 ticks ≈ 9s,
DEBOUNCED) → `/turn-end`. The DEBOUNCE matters here: the hermes gateway `session["running"]`
flag flips False MID-TURN (between tool calls / generation gaps), so the earlier
single-idle-read clear false-cleared `turn_busy` mid-turn → a `working`↔`online` FLAP.
Requiring N consecutive idle reads (any `working` read resets the streak) means a momentary
mid-turn idle blip can never clear the turn; the same debounce was applied to the in-flight
re-pulse probe. STATUS is pure-event (the seconds window no longer decides `working`); the
staleness window is the 30-min `TURN_BUSY_BACKSTOP_SECONDS` ceiling for a DROPPED end-event,
while the claim-gate keeps the short 120s `TURN_BUSY_STALE_SECONDS`. This is BRIDGE code: it
activates when the managed hermes agent's delivery loop respawns (relaunch its
`hermes-aify`, which kill-priors the old loop and loads the new bridge file). A loop
still claiming under the machine-global `hermes-managed-host-<machine>` id (no
`-<agentId>` suffix) is running old code.

## Hermes agent shows `online` while working

**Symptom.** A hermes agent is clearly mid-turn (TUI streaming, tools running)
but the dashboard/`comms_agent_info` reads `online`, not `working`.

**Cause.** Hermes turn detection used to be purely dispatch/hook-based: aify only
saw a turn via (a) an aify dispatch run, or (b) the `pre_llm_call` turn-start hook.

**Fix / status (2026-06-02, `2216c44`).** STATUS is pure-event (the event decides
`working`, not a window). For **managed** hermes this is resolved end-to-end by a
**continuous, bidirectional gateway-status detector** (`mcp/stdio/hermes-gateway-turn-detector.js`,
wired into `runDeliveryLoop`) — the hermes mirror of the claude transcript detector. It
reads the gateway session `status` (`session.active_list` → `working`/`idle`) every ~3s
for the WHOLE delivery-loop lifetime, NOT just inside a dispatch's in-flight window:
gateway `working` edge-triggers `/turn-start` (SET), gateway `idle` SUSTAINED (≥3
consecutive ticks ≈ 9s; tune via `AIFY_HERMES_GATEWAY_TURN_IDLE_DEBOUNCE`) POSTs
`/turn-end` (CLEAR). Because it runs continuously it covers dispatch, channel-woken,
**autonomous, AND direct-typed-in-the-TUI** turns (the old #172 "working but shown
online"), and because `working` is set off the live gateway-running truth there is **no
15-min in-flight-window (`REPULSE_WINDOW_MS`) cap** dropping a still-running long turn to
`online`. It keys ONLY on the gateway's own session truth (anti-feedback-loop), never the
aify server's derived status. The dispatch delivery pulse + in-flight re-pulse remain the
instant path; this detector is the continuous backstop in both directions; the staleness
window is the 30-min `TURN_BUSY_BACKSTOP_SECONDS` dropped-event ceiling. Relaunch
`hermes-aify` to load it. **Correction (2026-06-18 audit): resident hermes DOES arm this same
detector** — `startHermesGatewayTurnDetector` runs for resident hermes whenever
`AIFY_HERMES_GATEWAY_URL` is set (`server.js`), so an operator-launched `hermes-aify` with its
gateway URL exported (the normal wrapper path) gets the same continuous bidirectional turn
detector as managed. Hermes still exposes no upstream turn-END *hook* and the bridge's
bidirectional transcript turn-state detector keys on the *claude* transcript only, so the
gateway detector is the resident hermes turn-end mechanism. The residual is only a
**gateway-less** resident hermes (no `AIFY_HERMES_GATEWAY_URL`): it has no turn detector and
self-heals off `working` at the 30-min ceiling — but with no usable wake handle (wake-mode
`*-missing-handle`) it reads `offline`, not `available`, anyway — see KNOWN_ISSUES.md (#172).
No action needed if delivery itself works.

## EVERY managed-hermes dispatch fails "Queued >180s … up-but-deaf" (gateway host died — hermes 0.15.1 `--tui`)

**Symptom.** Every dispatch to a managed hermes agent fails with `Queued for >180s with
no live claimer … up-but-deaf or never started a worker`. The agent shows `available`;
the dashboard Console briefly flashes `[terminal attached pid=…]` then closes; the env-bridge
terminal logs only `[aify] spawned managed agent …` and nothing more; the terminal row ends
`reconciled_managed_ghost_console_dead_worker`.

**Cause (hermes 0.15.1, 2026.5.29).** Hermes 0.15.1 moved `--tui` to a **top-level** flag, so
the `dashboard` subcommand now **rejects** it (`error: unrecognized arguments: --tui`). The
bridge's `ensureGatewayHost` (`mcp/stdio/hermes-managed-host.js`) launched the gateway host as
`hermes dashboard --tui --port <P> --host 127.0.0.1 --no-open --skip-build`, which arg-errored
and died instantly → `ensure-host` 60s readiness timeout → wrapper `exit 1` → PTY closes → no
channel-sidecar claimer ever registers → the run is reaped as "no live claimer". The child's
stderr was `stdio:"ignore"`, which silently hid the arg error.

**Fix (`a363822` + correction `34bca11`/`b591a28`).** Dropped the rejected `--tui` flag from the
gateway-host args. ⚠ The `a363822` claim that plain `hermes dashboard` *"serves a working
`/api/ws`"* was WRONG — it only verified the index TOKEN, not the socket. `--tui` ALSO enabled the
dashboard EMBEDDED-CHAT feature that gates `/api/ws`: `web_server.py` closes `/api/ws` with code
**4403** when `_DASHBOARD_EMBEDDED_CHAT_ENABLED` is false, and that flag is set ONLY by `--tui` OR
the `HERMES_DASHBOARD_TUI=1` env. So after the `--tui` drop the gateway served the index (readiness
probe passed) but its `/api/ws` CLOSED → **"gateway websocket connection failed"** across ALL
managed hermes agents → TUI never attached → headless orphans (operator incident 2026-06-04).
**Real fix:** set `HERMES_DASHBOARD_TUI=1` in the gateway-host spawn env (crash-safe env equivalent
of the rejected flag — verified: `/api/ws` OPENs with it, CLOSEs without). **⚠ BINARY-DEPENDENT — CORRECTED
2026-06-05 (peer review):** that OPEN/CLOSE result held on the *pre-patch* 0.15.1 binary. A later hermes
0.15.1 patch (upstream `cae6b5486`, shipped by the same update that wiped `web_dist`) hardcodes
`_DASHBOARD_EMBEDDED_CHAT_ENABLED = True` and drops the gate — on the CURRENT binary plain `hermes dashboard`
serves `/api/ws`, and `HERMES_DASHBOARD_TUI=1` is a harmless no-op kept only as a fallback for older/pinned
hermes. The durable binary-agnostic guard is the `b591a28` `/api/ws` readiness probe. **Hardening (`b591a28`):**
`ensureGatewayHost` now opens `/api/ws` (not just the index) before declaring ready on the CLI
`ensure-host` path, so a regression of this class fails FAST at spawn instead of becoming a headless
orphan (env-gated `AIFY_HERMES_VERIFY_WS`, default on). The gateway child's stderr logs to
`~/.local/state/aify-comms/hermes-gateway-host-<port>.log`. **Deploy:** `git pull`,
`./install.sh --client hermes`, relaunch the agent's `hermes-aify` (or re-send — the env bridge
invokes the fixed managed-host.js fresh per spawn). NOTE: the visible `hermes --tui` TUI flag is
unchanged — only the hidden gateway-host launch dropped `--tui` and gained the env.

**Second cause of the SAME symptom (2026-07-02): hermes itself fails to boot after update
drift.** A stale hermes checkout (operator's was 1959 commits behind, then a partial
`hermes update`) crashed at launch — first `Installing TUI dependencies… TUI build failed /
npm error Missing script: "build"`, then `[hermes-managed-host] fatal: hermes dashboard at
http://127.0.0.1:<port>/ did not become ready within 60000ms` → wrapper exits → every managed
hermes agent bounces `up-but-deaf` while claude teammates on the same bridge work fine
(the hermes-only spread is the tell). The manager-side signature: `[NOT DELIVERED]` mirrors
for ALL hermes teammates at once. **Triage FIRST, before blaming dispatch:** run the managed
launch by hand — `HERMES_DASHBOARD_TUI=1 hermes dashboard --port 9199 --host 127.0.0.1
--no-open --skip-build` — a healthy hermes prints `HERMES_DASHBOARD_READY port=9199` within
seconds. **Recovery:** fix hermes itself (`hermes update` / force-update the checkout), verify
the command above, then restart each affected worker (dashboard Sessions → Restart, or
`POST /api/v1/sessions/{id}/control {"action":"restart"}`). No aify reinstall is needed —
hermes updates and `install.sh` write disjoint files; reinstall only if the hermes CLI
*interface* changed (as in the 0.15.1 `--tui` case above).

## Hermes native fallback ACP persistent session

Managed Hermes defaults to a wrapper-backed `hermes-aify` PTY that delivers through the visible-session gateway bind path. This section applies only when wrapper-backed delivery is disabled/unavailable or when the Console command is `aify://virtual-rpc/hermes`: the native fallback keeps a long-lived `hermes acp --accept-hooks` child per agent (`mcp/stdio/hermes-session.js`). Some symptoms specific to this path:

### Dispatch sits at `[hermes] thinking...` forever

`hermes acp` is alive but the prompt never resolves. Cause: hermes deadlocked inside the agent loop (provider stall, hook race, etc.), or a `session/request_permission` callback is being mishandled.

**Fix.** From the dashboard, Stop the agent's persistent worker (terminal Stop) and re-dispatch — the bridge will spawn a fresh `hermes acp`, run `initialize` + `session/new` again, and continue. The synthesized terminal row survives across the restart.

### `hermes acp handshake timeout (45000ms)`

The bridge spawned `hermes acp` but never got an `initialize` response within 45s.

- Confirm `hermes acp --check` exits 0 from the host (`pwsh: hermes acp --check`). If it prompts about shell-hook approval, your install is missing the `--accept-hooks` flag — the bridge passes it by default, but a custom `AIFY_HERMES_ACP_COMMAND` may have dropped it. Re-set: `AIFY_HERMES_ACP_COMMAND="hermes acp --accept-hooks"`.
- Check stderr tail in the dispatch_event for the run — the handshake-timeout error includes the last 200 chars of hermes's stderr. Common culprits: provider credentials missing, `~/.hermes/.env` not loaded, hooks-approval prompt blocking startup.

### Bridge declines hermes's terminal/* callbacks

If hermes asks the bridge to spawn a child process (`terminal/create`, etc.), the bridge replies with method-not-found by design (no in-bridge sandbox). Hermes should fall back to its own sandbox. If hermes errors out instead, configure hermes itself with a sandbox provider — the bridge will not host tool subprocesses.

## Hermes-aify wrapper fell through to plain hermes (Plan 5 Section A)

**Symptom.** A `hermes-aify` resident agent reports `wakeMode='hermes-missing-handle'`. From the operator's hermes shell, `echo $AIFY_HERMES_GATEWAY_URL` prints empty. Dispatches to the agent never wake it; the wrapper appears to have launched ok.

**Status note (`4611588`).** A resident hermes whose wake-mode ends in `-missing-handle` (no usable gateway handle) reads **`offline`**, not `available` — the status label and the sidebar dot share one live-state source, so they agree (they used to split: `available` label + red `unreachable` dot). So if you see this agent as `offline` with a red dot, that's the consistent missing-handle state, not a separate bug; recover it with the fix below. A genuinely-live resident (fresh bridge + usable `gatewayUrl` → `hermes-live`) reads `online` (or `available` before its worker starts). (Historically this surfaced as `stale`; that state was removed in the 2026-06-18 proof-based status rewrite.)

**Detection.**

```bash
ls -la ~/.local/state/aify-comms/hermes-aify-dashboard-*.log
# Look for files ~240 bytes, recently modified
cat ~/.local/state/aify-comms/hermes-aify-dashboard-*.log | head -5
# Expect: "✗ --skip-build was passed but no web dist found at: .../hermes_cli/web_dist"
```

If the log shows that line, the wrapper's `hermes dashboard --skip-build` probe died immediately (since hermes 0.15.1 this is plain `hermes dashboard` — `--tui` is no longer passed to the subcommand), `wait_for_http` timed out, and the wrapper falls back to plain Hermes without exporting the gateway URL. The MCP child then registers with no gateway env.

**Fix.** Re-run `./install.sh --client hermes` — current installs prebuild `hermes_cli/web_dist` once (commit `5057383`). Then restart the wrapper. Current wrappers print a visible WARNING when this fallback path triggers and preserve an explicit `hermes-aify --resume <id>` by falling back to `hermes --tui --resume <id>`.

## Hermes dispatch completes but open console does not move

**Symptom.** `hermes` resident or wrapper-backed runs show `prompt.submit` and may even complete in aify-comms, but the open `hermes-aify` terminal does not show the incoming message or reply. Older events may mention `session.resume`, `session.create`, `session id corrected`, or short `mem-*` gateway ids.

**Cause.** The bridge was forking a fresh in-memory Hermes sid over a second WebSocket. That can complete backend accounting, but it is not the operator-visible TUI session. The harness-console contract requires the active visible sid.

**Fix.** Current installs load the aify Hermes runtime plugin from `integrations/hermes-aify-plugin`; it registers `aify.session.bind_transport`, `aify.session.render_notice`, and preserves the wrapper-provided active-session file without editing Hermes source. Re-run `./install.sh --client hermes`, restart every open `hermes-aify`, then re-register from inside the visible terminal. A healthy run event says `visible session bound: <key> -> <sid>` before `prompt.submit`, and the visible TUI should show a boxed `aify-comms message` transcript/status notice before the assistant reply appears. If the saved handle was stale, you may first see `visible session key corrected: <old> -> <new>` or `visible session bind retry: <old> -> <current>`. If the bind/render method is missing, the plugin is disabled, or no active visible session can be selected, current bridges fail visibly and refuse hidden `session.resume` / `session.create` fallback.

If two Hermes resident agents run in the same cwd, they must still register
with different `runtimeConfig.gatewayUrl` values. Current bridges prefer the
current MCP process env over cwd runtime markers for Hermes. If both agents
show the same gateway URL, the bridge is old or one registration happened from
the wrong terminal; update/restart both wrappers and re-register each from its
own visible TUI.

## Hermes inter-agent delivery: queued forever / fabricated "delegated" reply / never renders in TUI / splits sessions (2026-06-03)

A cluster of resident+managed hermes delivery bugs, all resolved 2026-06-03 (commits de26a2e, 4cd9392, + the review-hazard follow-up). If you hit any of these, the wrapper/bridge/service is pre-fix — `git pull`, rerun `install.sh --client hermes`, relaunch `hermes-aify`, and rebuild the container.

- **Reply is `channel/resident dispatch delegated to hermes-managed-host.js delivery loop` (a fabricated placeholder), not a real agent turn.** Cause: the resident wrapper's MAIN bridge claimed the resident run and routed it through the dead `ChannelDelegatedController`, whose summary was auto-mirrored as the reply. Fix: `supportedExecutionModes` (mcp/stdio/dispatch-execution.js) no longer lets a hermes MAIN bridge claim `resident` — only its `channel-sidecar` loop (`hermes-managed-host.js run <agent>`) claims channel/resident hermes.
- **Run sits `queued` forever, `claim_bridge_id=''`, even with a live loop.** Cause: `_bridge_claim_block_reason` (service/routers/api_v2.py) `bridge_not_current` guard blocked the channel-sidecar's claim on the RESIDENT path (the carve-out was managed-only). Fix: exempt a declared channel-sidecar claim (`and not is_channel_sidecar_claim`).
- **Message delivers but never RENDERS in the visible TUI; opening the agent later shows it / one agent writes and you get TWO separate sessions.** Cause: a STALE inherited `HERMES_TUI_GATEWAY_URL` made `hermes --tui` spawn its OWN tui_gateway instead of attaching to the loop's gateway host (`gatewayClient.ts resolveGatewayAttachUrl` attaches iff that env is set), so the loop and the visible TUI were on different gateways/sessions. Fix: the wrapper `unset`s the stale gateway env before the fresh export. The visible TUI now attaches to the loop's host; `working` flips correctly (shared gateway). A bounded "no visible TUI attached to gateway" run-failure replaces infinite requeue.
- **Closed the resident `hermes-aify` but the agent still shows `online`, and the session split.** Cause: the gateway host + delivery loop were spawned detached and ORPHANED to init when the terminal closed; the orphan gateway kept a headless session (→ still `online`, and the loop polled it instead of your session). Fix: the wrapper runs the TUI as a child with a `trap` that reaps the loop→gateway host on exit; plus a loop-level "no TUI attached for N polls" teardown (`AIFY_HERMES_NO_TUI_TEARDOWN_CYCLES`, default 10) WITH a cold-start grace (`AIFY_HERMES_NO_TUI_GRACE_MS`, default 90s) so a slow first-launch TUI is never torn down before it attaches.
- **`hermes-aify --resume <id>` registers a DIFFERENT (stale) session handle.** Cause: with an explicit resume the active-session file/marker weren't seeded and discovery fell to a stale marker. Fix: the wrapper seeds active-file+marker with the resumed id (`hermes-managed-host.js resolve-session --explicit`), and `discoverSessionId` treats `AIFY_EXPLICIT_SESSION_HANDLE` as authoritative over the marker.
- **First message after a managed SPAWN sits `managed`/queued (spawn-initial).** Cause: created before the agent's sidecar/flag is up, so it stays `execution_mode='managed'` and the sidecar (claims only channel/resident) never picks it up. Fix: a 60s reconcile (`_reroute_orphaned_managed_channel_runs`) re-routes a queued `managed` run to `channel` once the target has a live channel-sidecar.
- **Switched a CODEX agent resident→managed and its dispatches sit `channel`/queued.** Cause: the switch re-attached a PTY to a leftover RESIDENT session instead of spawning a `managed-warm` worker, so no `managed-wrapper-child` claimer ever registered (and the 30s liveness beat demoted `bridge_kind` back to `resident`). Fix: switch/send now coldstart a managed-warm spawn for wrapper-backed runtimes, and the liveness beat can't demote a `managed-wrapper-child`/`channel-sidecar`. To unstick an already-switched agent: send it a message (it self-heals) or toggle managed→resident→managed once.
- **Duplicate / stale resident sessions on the dashboard you can't tell apart.** Cause: the resident session id is a hash of `session_handle`, so each relaunch with a new native id minted a new `resident_*` row while the old stayed `running`. Fix: a 60s reconcile (`_reconcile_duplicate_resident_sessions`) keeps the resident session whose owning bridge is freshest/live per agent and retires the rest (it never retires a session whose owning bridge is still within the resident lease).
- **Spawn fails "Workspace ... is outside this bridge's advertised roots" for a normal path under `/` or `~`.** Cause: `workspaceWithinRoots` (mcp/stdio/server.js) stripped `/` to empty (filtered out) and never expanded `~`. Fix: `/` is now match-all and `~` expands to `$HOME`.

## Hermes starts a FRESH session after an aify-comms restart

**Symptom.** A managed/resident hermes agent (e.g. `next-tech-lead`) resumes fine while
aify-comms stays up, but after you **close/start aify-comms** it restarts into a brand-new
session — "session not found" errors, lost chat history. Often only some agents; over time
"it just started giving fresh session".

**Cause 1 — ephemeral marker (`9353f86`, 2026-06-05).** The delivery loop (`waitForActiveSession`)
persisted the **ephemeral** runtime sid into the per-agent resume marker after every delivery,
instead of the **durable** `session_key`. Invisible while up (the ephemeral id still matched a
live `active_list` row), but on restart the gateway `active_list` is empty and the SessionDB
(`session.list`) is keyed by `session_key`, so the ephemeral marker matched nothing →
`runResolveSessionCli` cleared it → fresh session. Fixed: the loop now persists the durable
`rowResumeKey`.

**Cause 2 — dead `--resume` of a GC'd session (`5c1617a`, 2026-06-05).** Even with a durable-format
marker, hermes **GC's empty sessions** (`delete_empty_sessions`) — so a marker captured from a
session that never got a turn becomes a pointer to a session that no longer exists in the
SessionDB. The spawner passed that key straight to `hermes --resume <key>` via the **explicit-resume
short-circuit**, which seeded it WITHOUT checking the SessionDB → hermes errored `session not
found` and STRANDED the console (`stopped · Console attached`). Fixed: when a gateway is reachable,
`runResolveSessionCli` now **DB-validates** the resume id — a REAL id still wins over a stale
marker, a flaky gateway preserves operator intent, but a **definitively-absent id falls through to
a clean fresh start + clears the dangling marker** (no dead `--resume`). **Cause 2b — the wrapper
bypassed that validation for a command-line `--resume` (install.sh, 2026-06-05).** The `hermes-aify`
wrapper treats an explicit `--resume <id>` as authoritative and SKIPS `resolve-session`, so a stale
handle the env-bridge spawns as `hermes-aify --aify-agent X --resume <GC'd-key>` went straight to
`hermes --tui --resume` → "session not found" (the cms-senior-dev / next-* symptom; mp-senior-dev
worked because its handle was a real session). It was already CALLING `resolve-session --explicit`
but discarding the output. *Fix:* the wrapper now USES that DB-validated result — resumes a real id,
or starts FRESH cleanly when it is empty (GC'd). **Requires `install.sh --client hermes` + a wrapper
restart.**

**Cause 3 — the dead-handle CYCLE (`fresh on every stop+start`, install.sh 2026-06-05).** Symptom:
an agent loses its session on EVERY stop+start even after you send it real work. Root cause: the
wrapper exported `AIFY_SESSION_HANDLE=<requested handle>` to the in-session bridge BEFORE validating
it. So even when DB-validate started hermes FRESH, the bridge heartbeated the DEAD handle back
(status note "Session handle set by bridge-heartbeat"), aify stored it, and the env bridge
re-`--resume`d it next launch — an infinite loop that never captured the fresh session (verified:
`agents.session_handle` + `/tmp/aify-hermes-session-<agent>` both stuck on a GC'd key, ZERO sessions
for the agent's cwd in `~/.hermes/state.db`). *Fix:* on a fresh start the wrapper now **unsets**
`AIFY_SESSION_HANDLE`/`HERMES_SESSION_ID`/`AIFY_EXPLICIT_SESSION_HANDLE` so the bridge DISCOVERS the
fresh session's real id and reports THAT; on a validated resume it re-exports the resolved id. Breaks
the cycle on the next launch. **Requires `install.sh --client hermes` + wrapper restart.** KEY corollary: an agent whose workspace has **zero persisted sessions** in the SessionDB
(`get_session(key)`=None for all its markers; check `cwd`/title in `~/.hermes/state.db`) has nothing
to resume — **fresh is correct** for it, not a bug.

**Cause 4 — the resume POINTER never tracked the live session (the ROOT; `startResumeMarkerSync`,
2026-06-05).** Symptom: a directly-used agent starts a fresh "(untitled)" session on every restart
even though the TUI shows the old sessions as resumable ("1 live · 26 resumable"). Root cause: the
durable resume marker (`/tmp/aify-hermes-session-<agent>`) was only updated by `waitForActiveSession`
— which runs ONLY on an aify-comms DELIVERY. When the operator types in the visible TUI (or it mints
a new session), nothing converted the live session's EPHEMERAL id (TUI active-session file) to its
DURABLE `session_key` and wrote the marker — so it stayed on a stale/dead key and resolve resumed
that → fresh. Proven: the gateway's `session.list` returns the sessions (rows have `id`=durable key,
no `session_key`), `pickSessionRowById` matches, and a marker set to a real session resolves to it.
*Fix:* `startResumeMarkerSync` — a periodic (~20s) best-effort beat in the delivery loop that reads
the gateway's most-recent live session, takes `rowResumeKey` (durable), and writes the marker +
PATCHes the aify handle, so a restart resolves the live session. Composes with Causes 1-3.
**Requires `install.sh --client hermes` + wrapper restart.**

**Fix / remediation.** Reinstall the bridge (`install.sh --client hermes`) + **restart the
`hermes-aify` wrappers** (the resolver runs at launch). With `5c1617a` the dead markers are
**cleared automatically** on the next launch — the manual cleanup below is no longer required, but
still works as an immediate stopgap on a not-yet-restarted wrapper (durable keys contain
underscores `YYYYMMDD_HHMMSS_hex`; ephemeral is short bare hex; the session still lives in the
SessionDB, only the stale pointer is removed):

```bash
for f in /tmp/aify-hermes-session-*; do v=$(cat "$f" 2>/dev/null); case "$v" in *_*|"") :;; *) echo "rm $f ($v)"; rm -f "$f";; esac; done
```

Verify after a restart: `cat /tmp/aify-hermes-session-<agent>` stays a `YYYYMMDD_HHMMSS_hex`
key before AND after, and the gateway-host log shows `resolve-session … → <key> (marker(db-resumable))`, not `cleared stale marker; will start fresh`. To check whether an agent even HAS a
resumable session: `python3 -c "from hermes_state import SessionDB; print(SessionDB().get_session('<key>'))"` (run with hermes's venv python) — `None` means it's gone (fresh is correct).
