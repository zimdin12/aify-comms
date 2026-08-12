# aify-comms debug: Hermes sessions, gateway, ports and processes

Split out of `hermes.md` (2026-08-03) so one symptom does not load the whole catalogue. Sibling files are listed in the skill's routing table.

## Contents

- [Resident Hermes wakes, but dashboard shows no session evidence](#resident-hermes-wakes-but-dashboard-shows-no-session-evidence)
- [Managed Hermes dashboard send fails with `visible session not found`](#managed-hermes-dashboard-send-fails-with-visible-session-not-found)
- [Resident Hermes send says managed wrapper PTY is unavailable](#resident-hermes-send-says-managed-wrapper-pty-is-unavailable)
- [Resident Hermes reports live but send fails with `ECONNREFUSED 127.0.0.1:<port>`](#resident-hermes-reports-live-but-send-fails-with-econnrefused-127-0-0-1-port)
- [Hermes `gateway websocket connection failed` / two agents collide on one port](#hermes-gateway-websocket-connection-failed-two-agents-collide-on-one-port)
- [Managed hermes TUI shows, then drops with `gateway websocket connection failed`](#managed-hermes-tui-shows-then-drops-with-gateway-websocket-connection-failed)
- [Many `hermes.exe` processes for a few hermes agents](#many-hermes-exe-processes-for-a-few-hermes-agents)
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
per-agent Hermes channel-sidecar, or the sidecar claimed while its Console was
still stuck at `resuming...` for a stale saved handle. The
environment bridge only has stored `runtimeConfig.gatewayUrl` / `sessionHandle`,
and a not-ready wrapper has no bindable active visible session, so stale
records can point at an old gateway/session and fail visible-session binding.

**Fix.** Rebuild/restart the service and restart the host `aify-comms` bridge
so the environment bridge no longer advertises channel claim modes for
wrapper-backed Codex/Hermes. The service also rejects Hermes claims unless the
claimant bridge is registered as `bridge_kind='channel-sidecar'`, and it
blocks sidecar claims while the active Console still shows
`resuming...` rather than `ready`. Current terminal managers heal a long-stuck
Hermes resume by restarting once without `--resume`. Then **Restart** the
managed Hermes session or send again; the channel-sidecar should claim
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

Current boot cleanup also treats a live resident wrapper as authoritative process
evidence. Its associated process family is protected even if backend ownership
metadata is stale, so restarting an environment bridge must not reap the resident
gateway/TUI. If the WebSocket drops during a bridge restart, compare the installed
and running bridge with `aify-comms doctor --json`; a stale bridge is the first suspect.

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

**STOP reaps the whole triad; restart reaps the pile (2026-06-02, `f0bdaef`,
shutdown ownership hardened 2026-07-15).** You
no longer hand-kill stray `hermes.exe`. A dashboard **Stop** on a managed-hermes
agent now reaps the entire triad (gateway host + delivery loop + daemon),
agent-scoped — not just the console PTY (a resident/claude/other-runtime stop is
never touched). The environment bridge tears down only agents confirmed by a
**fresh service ownership snapshot**. It must never use its long-lived
`REMOTE_AGENT_STATE` cache as kill authority: after managed→resident takeover that
cache can still say `managed`, and the resident uses the same
`hermes-managed-host.js run <agent>` command shape. If the service is unavailable
during shutdown, teardown fails safe (kills nothing); the next boot sweep is the
backstop. A successful environment snapshot also prunes cached managed rows whose
current mode is resident. On replacement boot it first registers the new bridge,
then sweeps predecessor survivors, then adopts managed ownership and starts the
spawn loop. Never adopt/spawn before that sweep: a live predecessor handover can
otherwise leave the old triad owned by neither bridge. If registration or ownership
is unavailable, bootstrap retries without adopting or spawning. It sweeps survivors of a
crashed/killed predecessor
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
- **Run sits `queued` forever, `claim_bridge_id=''`, even with a live loop.** Cause: `_bridge_claim_block_reason` (service/routers/dispatch_messages/shared.py) `bridge_not_current` guard blocked the channel-sidecar's claim on the RESIDENT path (the carve-out was managed-only). Fix: exempt a declared channel-sidecar claim (`and not is_channel_sidecar_claim`).
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
