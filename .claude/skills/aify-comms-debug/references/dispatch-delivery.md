# aify-comms debug: Dispatch delivery: claims, steers and stuck runs

Split out of `dispatch-bridge.md` (2026-08-03) so one symptom does not load the whole catalogue.

## Contents

- Managed claude instance proliferation / a managed agent killed my session
- Dispatches stay `queued`/`delivered`, never claimed (delivery silently stalls)
- Runs view: routine `delivered` runs show a blank summary (expected)
- Managed run FAILED with "turn is presumed dead (model 429 / interrupt / stall)"
- Claude: wake mode stuck at `claude-needs-channel`
- Run stuck `running`, `comms_run_interrupt` has no effect
- `comms_send(steer=true)` stayed unread or looked queued behind itself
- Run summary says `Auto-healed: bridge "<old>" replaced by "<new>"`
- Queued managed run never claimed (Plan 5 Section B)
- Team stranded after a restart: runs stuck `claimed`, never delivered
- Install.sh on Windows / Git Bash
- Send to resident Claude rejected as "no live wake path"
- In-flight run cancelled with "bridge X is not the current agent bridge Y"
- Dashboard chat routes native managed work through the wrong console
- Managed claude dispatch cancelled with "capabilities do not include managed-run"
- Spawn-time initial message to managed claude sits queued forever

## Managed claude instance proliferation / a managed agent killed my session

**Symptom.** Many `claude.exe --resume <same id>` for one agent; or the operator's
own resident claude got force-closed when another agent launched.

**Cause.** Managed claude churns terminals and a `failed` terminal isn't reaped, so
native `claude.exe` orphans accumulate. The kill-prior reaper is **agent-scoped**
(kills only `claude.exe` whose parent wrapper is `--aify-agent <thatAgent>`), so it
can never kill a different agent or a resident session — even if two agents share a
`--resume` id. Root prevention: the cross-agent **session-collision guard** parks a
handle a different LIVE agent already owns (`session-collision` note) instead of
binding it. **Fix:** pull/rebuild + restart `aify-comms`; the reaper collapses each
agent to one instance on next managed launch.

## Dispatches stay `queued`/`delivered`, never claimed (delivery silently stalls)

**Symptom.** Messages to a claude/hermes agent sit `queued` and never deliver;
the agent looks `online` but nothing happens. Restarting the wrapper doesn't fix
it. Often paired with a co-located teammate that DOES receive.

**Causes + fixes (all landed 2026-05-31).**
- **Resident sidecar released by mistake.** The mode-FSM "release" used to fire
  for any `channel-sidecar` claim on a non-managed agent, killing the resident
  delivery sidecar's poll loop. Now gated on `driver_state != 'driving'` (a live
  resident driver is `driving`). Fixed server-side — but a sidecar that already
  exited needs ONE wrapper restart to resume.
- **Channel-sidecar bridge superseded → claims blocked.** During managed-PTY
  churn the sidecar's bridge briefly went stale and the wrapper-child
  registration superseded it, permanently blocking claims. Now the complementary
  channel-sidecar↔wrapper-child pair is never superseded, and a live sidecar
  poll **self-heals** (un-supersedes) its own row. Recovers without a restart.
- **Machine-global sidecar id collided across co-located agents.** Sidecar bridge
  ids are now per-agent (`channel-<machine>-<agentId>`). Reinstall the `*-aify`
  wrappers + restart sessions to pick it up.

**Verify (read-only):** `docker exec aify-comms-service python -c "..."` →
`SELECT id,agent_id,superseded_by,last_seen FROM bridge_instances WHERE
bridge_kind='channel-sidecar'`. A healthy agent has a fresh, non-superseded row.
Queued runs: `SELECT status,COUNT(*) FROM dispatch_runs WHERE target_agent='<id>'
GROUP BY status`.

## Runs view: routine `delivered` runs show a blank summary (expected)

**Symptom / question.** A successful `delivered` run in the Runs view has an
empty `summary`, so the Runs list looks quiet.

**Cause.** Intentional (D2): routine successful deliveries now carry an empty
`summary` to keep the Runs view from filling with redundant "Delivered to ..."
notes. Failed/cancelled/noteworthy runs still carry their summary, so real
problems remain visible. A blank summary on a `delivered` run is not a dropped
result — the team-visible answer is still the message/reply flow.

## Managed run FAILED with "turn is presumed dead (model 429 / interrupt / stall)"

**Symptom.** A `require_reply` run to a managed agent (usually hermes) shows `failed`
with that reason ~45 min after delivery, and the sender got a mirrored failure notice —
but the agent's console looks fine / idle.

**Cause.** Intentional backstop (2026-07-10, `_fail_stranded_delivered_reply_runs`). The
worker's turn DIED without replying — a model-429 killed it before any work, or a mid-turn
interrupt/stall ended it — so it never sent `comms_send` and never emitted a clean turn-end,
leaving the run stuck `delivered` forever (which reads as "the agent is ignoring the
contract"). Past `stranded_reply_fail_minutes` (default 45, well beyond the 10/20/30 reminder
cycle) reconcile FAILS it with the cause so the strand is visible instead of silent. It is
keyed on staleness (not `turn_busy`) so it's robust while the hermes turn-status flaps, and it
SKIPS a run the agent is actively working. **Recovery:** re-dispatch the ask (the model
pressure that killed the turn — often a per-session/per-key 429 limit distinct from the pool
number, or a transient stall — has usually eased); resume-restart the agent if its session is
wedged post-interrupt. Set `stranded_reply_fail_minutes=0` to disable the backstop.

## Claude: wake mode stuck at `claude-needs-channel`

**Symptom.** `comms_agent_info` reports `wakeMode: claude-needs-channel` even though you launched with `claude-aify`. A previous agent may have worked around it by manually writing a runtime marker with a live `claude.exe` Windows PID — that's the fingerprint of this bug.

**Cause.** For a long time the `claude-aify` bash wrapper wrote the runtime marker itself with `pid=$$`. On Git Bash for Windows, `$$` is the MSYS shell PID, not a Windows process ID. The bridge's `isProcessAlive` check uses `process.kill(pid, 0)`, which on Windows only understands real Windows PIDs, so it returned false and `listRuntimeMarkers` **auto-deleted the marker on the next read**. Every claude-aify session on Windows silently lost its marker within a second and fell through to `claude-needs-channel`. Same root cause made `codex-aify` markers disappear, which is why the Codex auto-discovery path kept falling through to poisoned threads.

**Fix (shipped).** The marker is now written by the long-lived bridge process (`claude-channel.js` for Claude, `server.js` for Codex when `AIFY_CODEX_APP_SERVER_URL` is set) using node's real `process.pid`. Claude markers also include the parent Claude process PID, so a plain Claude tab cannot accidentally bind through another tab's channel. The wrappers no longer touch markers. Requires: pull, restart the affected wrapper (`claude-aify`, `codex-aify`, `omp-aify`, or `pi-aify`). Check `C:\Users\<you>\.local\state\aify-comms\runtime-markers\` after a fresh launch — the file should persist and its `pid` field should match a live node child of the runtime.

**Fix (recovery when you hit this).** Start the same Claude session through `claude-aify`, then re-register from that session:

```
comms_register(agentId="my-agent", role="coder", runtime="claude-code", cwd="C:/path/you/are/in")
comms_agent_info(agentId="my-agent")
```

On Windows, the installer creates both a Bash `claude-aify` and a `claude-aify.cmd` shim. From PowerShell / cmd prefer the `.cmd`; from Git Bash either is fine.

## Run stuck `running`, `comms_run_interrupt` has no effect

**Symptom.** A dispatch is marked `running` but nothing is happening. `comms_run_interrupt` returns ok but the run never moves.

**Cause (Codex / managed sessions).** Either the bridge that owned the run has died (crash, machine sleep, network drop), or Codex accepted `turn/start` and then stopped emitting runtime notifications. `comms_run_interrupt` works by enqueueing a control the owning bridge polls for — if the bridge is gone, no one claims the control.

If the last event is `Started mcpToolCall`, do not assume it is WSL-specific. The Codex turn is inside a tool call. A normal `comms_send` / `comms_inbox` call should return quickly. The deprecated `comms_listen` long-poll can intentionally wait and should not be used in managed runs; older builds let mistaken listen calls wedge the run until the outer quiet watchdog fired. Current MCP builds apply a bounded timeout to ordinary remote HTTP tool calls (`AIFY_HTTP_TIMEOUT_MS`, default 20000ms), so the model can see the tool error and use the prompt's plain-text fallback handoff instead of sitting forever.

Current managed Codex config also sets `tool_timeout_sec = 25`, `disabled_tools = ["comms_listen"]`, and `AIFY_MANAGED_DISPATCH=1` for its aify-comms MCP server. Managed Codex runs now use Codex's unattended bypass sandbox profile by default; `approvalPolicy=never` plus `workspace-write` can still cause non-interactive MCP calls to be cancelled or wedge without a normal tool result. The bridge also copies the bundled `aify-comms` and `aify-comms-debug` skills into the managed Codex home. Current WSL/Linux builds kill the whole managed runtime process tree on timeout/interrupt/stop; older builds killed only the parent app-server and could leave orphan MCP children behind. If a managed Codex run still sits at `Started mcpToolCall aify-comms` for minutes after updating, or says those skills are missing, the bridge process is stale or the managed CODEX_HOME/app-server is still running old settings; pull/rebuild/restart the WSL bridge so it regenerates the managed Codex config, skills, and launch policy.

**Codex quiet watchdog (current build).** Managed runtimes have a long hard timeout for genuinely long work, and managed Codex also treats a turn as stalled if no Codex runtime notification or stderr line is seen for the quiet timeout window after the last activity. Defaults are 12 hours hard timeout and 30 minutes quiet timeout. A narrower aify-comms MCP watchdog fails stuck `mcpToolCall aify-comms` items after 90 seconds by default because comms tool calls should either return quickly or fail clearly. Change per agent with `runtimeConfig.timeoutMs`, `runtimeConfig.quietTimeoutMs` / `runtimeConfig.silenceTimeoutMs`, and `runtimeConfig.mcpToolTimeoutMs` / `runtimeConfig.commsToolTimeoutMs`; set quiet timeout to `0` only for agents expected to run very long silent commands, and set MCP tool timeout to `0` only while debugging the MCP transport. The run is failed cleanly, the managed runtime process tree is terminated, stale controls are failed, and a required handoff is mirrored back to the sender.

**Bad root artifacts.** Older launchers could accidentally treat `--help` or another flag-like argument as an advertised workspace root. Current launchers make `aify-comms --help` print usage and reject unknown options, and the service ignores flag-like roots from stale heartbeats. If an environment still shows a root like `--help`, restart the bridge after reinstalling, or edit/reset the environment roots in the dashboard.

**Cause (Claude channel).** On older bridge code, the channel bridge claimed run records and left them `running` indefinitely after delivery — it had no way to track Claude's progress. On current code, the channel bridge marks those runs `delivered` / awaiting explicit reply, so delivery telemetry does not hold the agent in `working`. If you still see it, the service or host bridge is running pre-fix code — rebuild the service and restart the affected `claude-aify` / managed Claude backing.

**Auto-recovery (current build).** When a replacement bridge polls `/dispatch/claim` for the same agent, the server gives a recently claimed run a short grace window before declaring it stale. During that window the replacement bridge sees `blockedBy.reason = "active_run_owned_by_previous_bridge"` and should retry. If the previous bridge does not finish, the server then marks the orphaned run failed automatically and existing queued run-control work may be claimed normally. Normal `comms_send` will not create additional queued work while the target is blocked.

For older dispatch-backed messages, the original inbox message may still exist. For current normal `comms_send`, failed live delivery writes no message row, so retry after the agent is startable.

**Manual fix (if no bridge is polling or the current run predates the watchdog).** Cancel the run directly through the HTTP API:

```bash
curl -X PATCH http://localhost:8800/api/v1/dispatch/runs/<run_id> \
  -H "Content-Type: application/json" \
  -d '{"status":"cancelled","error":"Bridge died, orphaned run"}'
```

Afterwards, restart the affected wrapper to bring a live bridge back online.

## `comms_send(steer=true)` stayed unread or looked queued behind itself

**Symptom.** A steer message lands in the inbox unread, the tool output says it was queued behind the same run ID, or a steer sent during a bridge replacement seems to disappear.

**Cause.** Older server code treated a steered result like a newly queued run and could target a stale active run that was still owned by a superseded bridge. In that state the source inbox message had no completed steer control to mark it read.

**Fix (current build).** Pull latest and restart the target bridge (`codex-aify` / `claude-aify`) so it is running the steer-tracking fix. Current behavior is:
- if there is a live steer-capable active run, the message becomes a steer control and the inbox copy auto-marks read when the control completes
- if the target is busy but not steer-capable, the message queues or merges as next-turn work
- resident `claude-aify` steering is channel-based: the channel bridge emits a `notifications/claude/channel` event into the live Claude session; this is not the same mechanism as Codex `turn/steer`
- if the target cannot accept live delivery, `comms_send` returns a not-sent notice instead of queueing future work
- if the runtime does not support steering, the send follows the normal live-start gate

If you still see the old behavior after update, capture the run ID plus `/api/v1/dispatch/runs/<id>` and `/api/v1/agents/<agent>` output.

## Run summary says `Auto-healed: bridge "<old>" replaced by "<new>"`

**Symptom.** A dispatch run shows an auto-heal summary like `Auto-healed: bridge "old" replaced by "new"` or `Auto-healed before steer...`.

**Cause.** The server saw a new live bridge polling for the agent while the DB still had an active run claimed by an older bridge. If that run was older than the bridge-replacement grace window, the server treated it as orphaned and failed it to unblock the queue.

**Fix.** Usually no repair is needed beyond shutting down the stale bridge and re-registering from the live session. This is a recovery path, not silent data loss for older dispatch-backed messages. If it happens seconds after a reconnect, update and restart the dashboard service: current builds wait briefly before failing another bridge's active run. Current normal sends will fail fast instead of queueing fresh work behind stale state. If this repeats on every dispatch, an old bridge is probably still polling; current builds should block it with `bridge_not_current` before it can claim fresh work.

## Queued managed run never claimed (Plan 5 Section B)

**Symptom.** A managed codex / hermes / pi agent (e.g. graph-senior-dev, hermes-test, pi-aify managed) is registered, the bridge is alive and heartbeating, the wrapper PTY is running — but `comms_send` messages stay queued indefinitely. No claim event, no controls recorded.

**Detection.** Query the service DB for the agent's dispatch runs:

```bash
docker exec aify-comms-service python -c "
import sqlite3, glob
db = sorted(glob.glob('/data/*.db'))[-1]
c = sqlite3.connect(db)
for r in c.execute(\"SELECT id, runtime, status, execution_mode, claim_bridge_id, created_at FROM dispatch_runs WHERE agent_id='YOUR-AGENT-ID' ORDER BY created_at DESC LIMIT 5\"):
    print(r)
"
```

If you see rows with `status='queued'`, `execution_mode='channel'`, and `claim_bridge_id=''` more than a few seconds old, this is the Plan 5 Section B gap: the server routed the run to channel-mode but the bridge whitelist (`_CHANNEL_CLAIM_RUNTIMES` in `api_v2.py`) didn't include that runtime, so its bridge can't claim.

**Fix.**
1. Confirm Plan 5 is deployed — grep the container for `_CHANNEL_CLAIM_RUNTIMES`:
   ```bash
   docker exec aify-comms-service grep -n "_CHANNEL_CLAIM_RUNTIMES" /app/service/routers/api_v2.py
   ```
   Expect a line defining the set as `_CHANNEL_MANAGED_RUNTIMES | {"codex", "hermes", "pi"}`. Missing → rebuild the service.
2. Check that the affected runtime is in `managed_via_wrapper`:
   ```bash
   curl -s http://localhost:8800/api/v1/settings | python -m json.tool | grep -A3 managed_via_wrapper
   ```
   Should list `"codex"` and `"hermes"` (current default). Pi is intentionally excluded from wrapper mode and uses managed RPC.
3. If wrappers are older than commits `3bcbac2` / `0beab57`, run `./redeploy.sh` to refresh installed `*-aify` wrappers and restart any host bridges. Re-dispatch — the queued run should claim within one poll cycle (~3s).

## Team stranded after a restart: runs stuck `claimed`, never delivered

**Symptom.** After killing/restarting wrappers (or a host bridge restart) the
team stops moving: dispatch runs sit at `claimed`, never `delivered`; agents
show `working`/busy; and the manager that sent the pings never gets a reply.
New sends queue behind the stuck run.

**Cause.** A run was `claimed` by a bridge that then died before delivering
(common after a mass wrapper kill — e.g. killing all `hermes.exe`). The claim
held the agent busy, but the claiming bridge was gone, so nothing ever delivered
or closed the run.

**Fix (2026-06-02, `a76afb5` + lifecycle batch).** Two layers now prevent this:

- **Restart = clean slate.** Restarting `aify-comms` is no longer the trigger for a
  stranded team — it is the cure. The env bridge tears down all managed sessions it
  owns on shutdown and boot-sweeps any survivors of a crashed predecessor (see
  "Restarting aify-comms kills all managed sessions (by design)"), so a restart
  leaves no dead claimer holding a busy agent. Managed sessions re-spawn fresh.
- **Requeue + queued-run backstop.** The 60s reconcile loop requeues a run that is
  `claimed` (claimed > 90s ago), has no `delivered` event, and whose claiming bridge
  (`claim_bridge_id`) is dead → back to `queued` so a live bridge re-claims and
  delivers it (recovered, not failed; runs BEFORE the orphaned-managed-run reaper).
  And a `queued` run whose target has **no live claimer** past the backstop window
  (~180s) is FAILED with an actionable error and mirrored back to the sender, so a
  genuinely deaf target no longer piles up an indefinite queue. Note (2026-06-02):
  a send to such a target now QUEUES rather than failing fast — this backstop is the
  sole net (see "Send to a managed agent with no live claimer").

On a pre-fix build, rebuild the service; for an immediate unstick, restart the target
wrapper (or `aify-comms`) so a live bridge re-claims.

## Install.sh on Windows / Git Bash

Current installer behavior:

- `--with-hook` is Git Bash aware. It writes native Windows hook paths without MSYS path mangling, so the old `C:\c\Users\...` failure should not require manual `settings.json` or `hooks.json` edits.
- The installer creates Bash wrappers and `.cmd` shims in `%USERPROFILE%\.local\bin`, including `aify-comms.cmd`, `claude-aify.cmd`, `codex-aify.cmd`, `omp-aify.cmd`, and `pi-aify.cmd` when the matching client is installed.
- `codex-aify` defaults to Codex's unattended bypass profile and passes `--dangerously-bypass-approvals-and-sandbox` to both the local app-server and visible remote TUI. Use `codex-aify --safe` / `--no-auto` / `--no-dangerous-permissions` only when deliberately debugging Codex permission behavior. The wrapper does not use the older `--full-auto` flag.
- `claude-aify` also preserves normal permissions by default. Use `claude-aify -auto` to add `--dangerously-skip-permissions`.
- The `.cmd` shims prepend Git's Unix binary directories when they can find Git, so `sed`/`bash` should be available even when PowerShell only had `C:\Program Files\Git\cmd` on PATH.

If Windows still cannot find `aify-comms.cmd` after install:

```powershell
$env:Path += ";$env:USERPROFILE\.local\bin"
& "$env:USERPROFILE\.local\bin\aify-comms.cmd"
```

If Claude is installed but `claude.cmd` is missing, the wrapper falls back to `claude` when available. Prefer the native Windows Claude Code install when possible, then restart Claude/Codex after reinstalling aify-comms.

If Hermes shows unavailable while `hermes-aify.cmd` exists, check the underlying runtime separately. The wrapper is not the Hermes executable. The environment bridge advertises Hermes only when `hermes` resolves from the bridge process PATH, or when `AIFY_HERMES_COMMAND` / `HERMES_COMMAND` points at the real executable. From PowerShell, run `Get-Command hermes` and `Get-Command hermes-aify.cmd`; if only the wrapper is found, set `AIFY_HERMES_COMMAND` to the absolute Hermes executable path and restart the Windows bridge.

## Send to resident Claude rejected as "no live wake path"

**Symptom.** `comms_send(...)` to a resident claude-code agent returns "Message was not sent because one or more recipients cannot start live work now" even though the wrapper is running and `comms_agent_info` shows the agent online with a recent `last_seen`.

**Cause.** The agent's `runtime_config.channelEnabled` is not `true`, so `_row_capabilities` at `api_v2.py` strips `resident-run`, `interrupt`, and `steer` from the capabilities at every read. Preflight then sees `["managed-run","resume"]` and concludes there's no live wake path. This used to require manual DB patches every time a claude-aify session re-registered.

**Fix.** Restart the claude-aify wrapper from a current install — current `claude-aify` exports `AIFY_CHANNELS_ENABLED=1`, and `mcp/stdio/server.js` includes `runtime_config.channelEnabled=true` in the `/agents` register call. Then re-register from the live wrapper or launch it with `--aify-agent <id>`. Do not repair this by hand-editing the database; rebuild/redeploy and restart the wrapper so the bridge heartbeat, runtimeConfig, and claim loop all match.

## In-flight run cancelled with "bridge X is not the current agent bridge Y"

**Symptom.** A managed run was actively producing output, then the bridge log shows `Active run owner bridge X is not the current agent bridge Y` and the run is marked cancelled mid-turn. The agent re-registered (often because a nested RPC child or sibling wrapper PTY started up) and the new bridge_instance superseded the active one.

**Cause.** `bridge_instances` supersession used to be scoped to `(agent_id, machine_id)` only. A sibling registration with a different `session_mode` or `session_handle` triggered supersession against the unrelated live bridge. The most common trigger was a bridge-spawned wrapper PTY (e.g. pi-aify hosting `omp --mode rpc`) that auto-detected its TTY and registered as resident, then collided with the real resident bridge.

**Fix.** Already fixed in the post-`4dbb2e2` `_record_bridge_registration` helper (supersession narrowed to the full `(agent_id, machine_id, runtime, session_mode, session_handle)` tuple) + `terminal-env.js` declaring `AIFY_SESSION_MODE=managed` for bridge-spawned PTYs. If you still see it, check the agent's bridge_instances rows — different `session_mode` or `session_handle` between them means the new code is doing the right thing; the legacy supersession was likely cleared on a prior run.

## Dashboard chat routes native managed work through the wrong console

**Symptom.** Sending to a managed Pi/OpenCode agent, or to Codex/Hermes with wrapper backing disabled, creates `consoleDeliveries` / `dispatch_mode='terminal'` and injects dashboard text into a PTY instead of creating a normal native managed run. Operator sees a raw console that is not the real managed delivery owner.

**Cause.** The legacy `/dispatch` path treated `managed_terminal_backing_enabled=true` as permission to type into a PTY for every native-managed runtime. That is only valid when the explicit escape hatch `insert_messages_via_console=true` is enabled. Default managed Pi/OpenCode should stay on their native controller / virtual-terminal paths; Codex/Hermes only use wrapper PTYs when `managed_via_wrapper` selects them.

**Fix.** Update/rebuild the service. Current builds only use PTY-input delivery when `insert_messages_via_console=true`; otherwise `/dispatch` persists `execution_mode='managed'` for native managed runtimes and the bridge controller claims it. For wrapper-backed Codex/Hermes, the environment bridge is blocked from claiming `execution_mode='channel'` directly: Codex's wrapper child claims it, while Hermes's per-agent channel-sidecar claims it.

**Claude note.** Managed Claude still needs a `claude-aify` PTY host, but default delivery is channel notification (`insert_messages_via_console=false`), not raw stdin typing. If Claude channel runs sit queued, check that the wrapper PTY is running and the env advertises `claude-code` terminal support.

## Managed claude dispatch cancelled with "capabilities do not include managed-run"

**Symptom.** Send to a managed claude-code agent with `insert_messages_via_console=false (the default channel-route mode; earlier name claude_managed_channel_only=false)`. The dispatch_event row reads `agent capabilities do not include "managed-run"` and the run is cancelled before delivery. The agent's capabilities show `["resume", "interrupt", "spawn"]` (no `managed-run`) and `runtime_config.channelEnabled=true`.

**Cause.** Default capabilities for managed claude omit `managed-run` by design (claude has no headless managed-run API). Pre-`a4498a6`, `_agent_execution_mode` rejected dispatches on the missing cap before the channel branch could fire.

**Fix.** Already fixed in `a4498a6` — the cap-check is skipped when runtime is `_CHANNEL_MANAGED_RUNTIMES` AND `runtime_config.channelEnabled=true`. Container needs rebuild to pick up the api_v2.py change. Do not add `managed-run` by hand in the database; that can mask the real channel/wrapper health problem.

## Spawn-time initial message to managed claude sits queued forever

**Symptom.** `comms_spawn(runtime="claude-code")` registers the agent + spawns wrapper PTY successfully, but the spawn's `initialMessage` dispatch_run has `status='queued'` and `execution_mode='managed'` — never claimed by `claude-channel.js`. With `insert_messages_via_console=false (the default channel-route mode; earlier name claude_managed_channel_only=false)`, the run should have `execution_mode='channel'`.

**Cause.** Pre-`a4498a6`, `update_spawn_request`'s running-transition handler called `_create_dispatch_runs(...)` to create the initial-message run, but did NOT call `_apply_channel_only_to_claude_runs(...)` afterward. The run stayed `execution_mode='managed'` even with the channel-only setting on. The same gap existed in the auto-mirrored handoff path at line 4912.

**Fix.** Already fixed in `a4498a6` — both call sites now apply channel-only post-create. For runs created before the fix that are stuck queued, prefer cancelling/retrying after rebuilding and restarting the bridge/wrapper. Avoid manual `dispatch_runs` SQL unless you are doing a one-off forensic repair and have captured the original run state.

