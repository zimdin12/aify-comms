# aify-comms troubleshooting: Dispatch, bridge health, claude channel & install

## Contents

- [Managed claude instance proliferation / a managed agent killed my session](#managed-claude-instance-proliferation-a-managed-agent-killed-my-session)
- [Dispatches stay `queued`/`delivered`, never claimed (delivery silently stalls)](#dispatches-stay-queueddelivered-never-claimed-delivery-silently-stalls)
- [Managed worker "launches then dies", stuck `available` — reaped mid-boot during a slow SessionStart hook](#managed-worker-launches-then-dies-stuck-available-reaped-mid-boot-during-a-slow-sessionstart-hook)
- [Runs view: routine `delivered` runs show a blank summary (expected)](#runs-view-routine-delivered-runs-show-a-blank-summary-expected)
- [Claude: wake mode stuck at `claude-needs-channel`](#claude-wake-mode-stuck-at-claude-needs-channel)
- [Claude managed run fails: `Session ID ... is already in use`](#claude-managed-run-fails-session-id-is-already-in-use)
- [Managed spawned agent workspace is stored as `\home\dev\...`](#managed-spawned-agent-workspace-is-stored-as-homedev)
- [Claude/Pi managed run fails: `spawn "/path/to/claude-or-omp" ENOENT`](#claudepi-managed-run-fails-spawn-pathtoclaude-or-omp-enoent)
- [Machine ID shows `win32:unknown-host`](#machine-id-shows-win32unknown-host)
- [Run stuck `running`, `comms_run_interrupt` has no effect](#run-stuck-running-comms_run_interrupt-has-no-effect)
- [`comms_send(steer=true)` stayed unread or looked queued behind itself](#comms_sendsteertrue-stayed-unread-or-looked-queued-behind-itself)
- [Run summary says `Auto-healed: bridge "<old>" replaced by "<new>"`](#run-summary-says-auto-healed-bridge-old-replaced-by-new)
- [Team stranded after a restart: runs stuck `claimed`, never delivered](#team-stranded-after-a-restart-runs-stuck-claimed-never-delivered)
- [Install.sh on Windows / Git Bash](#installsh-on-windows-git-bash)
- [Send to resident Claude rejected as "no live wake path"](#send-to-resident-claude-rejected-as-no-live-wake-path)
- [In-flight run cancelled with "bridge X is not the current agent bridge Y"](#in-flight-run-cancelled-with-bridge-x-is-not-the-current-agent-bridge-y)
- [Dashboard chat routes native managed work through the wrong console](#dashboard-chat-routes-native-managed-work-through-the-wrong-console)
- [Managed claude dispatch cancelled with "capabilities do not include managed-run"](#managed-claude-dispatch-cancelled-with-capabilities-do-not-include-managed-run)
- [Spawn-time initial message to managed claude sits queued forever](#spawn-time-initial-message-to-managed-claude-sits-queued-forever)
- [Channel-routed claude dispatches stay queued forever (resident or managed)](#channel-routed-claude-dispatches-stay-queued-forever-resident-or-managed)
- [Channel/resident dispatches silently fail on Windows — bridge can't reach localhost:8800](#channelresident-dispatches-silently-fail-on-windows-bridge-cant-reach-localhost8800)
- [Queued managed run never claimed (Plan 5 Section B)](#queued-managed-run-never-claimed-plan-5-section-b)
- [Stale session handle causing prompt.submit failures (Plan 6 A)](#stale-session-handle-causing-promptsubmit-failures-plan-6-a)
- [Fixed check: wrapper-backed channel claim must be child-owned](#fixed-check-wrapper-backed-channel-claim-must-be-child-owned)
- [Managed claude freezes on boot at a prompt (resume / compaction / permissions)](#managed-claude-freezes-on-boot-at-a-prompt-resume-compaction-permissions)
- [Run failed with a "provider rate-limiting, not your request — retry shortly" notice](#run-failed-with-a-provider-rate-limiting-not-your-request--retry-shortly-notice)
- [General escalation](#general-escalation)

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

## Managed worker "launches then dies", stuck `available` — reaped mid-boot during a slow SessionStart hook

**Symptom.** A managed claude (or hermes) worker "launches then dies": it ends up `available`
with no visible terminal, the terminal row error is `reconciled_managed_ghost_console_dead_worker`,
and the dashboard Console's last visible line is `Running SessionStart hooks…… (Nm Ns)`. Often
intermittent — "now it stays up" after a while.

**Cause.** The ghost-console reaper (`_reconcile_managed_worker_hygiene`, B1) declared a managed
worker dead purely from `_has_live_channel_sidecar` being false. But the claimer bridges
(`claude-channel.js` sidecar / managed-wrapper-child MCP) register only AFTER claude finishes init,
which includes SessionStart hooks that can run for MINUTES (observed: a 1m28s one-time operator-plugin
dep install, e.g. an `observability` plugin's `install-deps.js`). During that boot the PTY is alive
and STREAMING the hook spinner, but no claimer exists yet — so the sidecar check could not tell
"booting" from "dead" and reaped the live worker mid-boot. Rapid operator restarts compounded it (each
restart's kill-prior reaped the prior still-booting attempt). Once the one-time setup completes,
SessionStart hooks drop to ~3s and the worker stays up — hence the "now it stayed up" intermittency.

**Fix (`6664022`, deterministic — NOT a timer).** The reaper now declares a worker dead only when ALL
real process-liveness signals are absent: no live channel-sidecar AND no live managed-wrapper-child AND
no fresh terminal output activity (`terminal_sessions.updated_at`, bumped by every bridge output frame
via `_append_terminal_output`). A booting/streaming PTY is provably alive → never reaped; a genuinely
dead worker (output stopped) still is. Reuses `MANAGED_ORPHAN_GRACE_SECONDS`.

**Operator notes.** (a) A managed worker's FIRST launch can be slow if an operator plugin runs a
one-time SessionStart setup (dep install) — that is now tolerated, just wait it out. (b) Don't
rapid-restart a fresh managed worker — give it ~30–60s to finish SessionStart hooks before it becomes a
claimer. (c) Episodic-memory SessionStart hooks remain the WSL-crash risk and should stay disabled.

## Runs view: routine `delivered` runs show a blank summary (expected)

**Symptom / question.** A successful `delivered` run in the Runs view has an
empty `summary`, so the Runs list looks quiet.

**Cause.** Intentional (D2): routine successful deliveries now carry an empty
`summary` to keep the Runs view from filling with redundant "Delivered to ..."
notes. Failed/cancelled/noteworthy runs still carry their summary, so real
problems remain visible. A blank summary on a `delivered` run is not a dropped
result — the team-visible answer is still the message/reply flow.

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

## Claude managed run fails: `Session ID ... is already in use`

**Symptom.** A dashboard-managed Claude run fails immediately with an error like `Session ID e5b70d2b-b700-4b77-a6fe-d65ccb8f84c6 is already in use`.

**Cause.** For old data/old bridge builds, the common managed-run cause was using Claude Code's `--session-id` flag for a session that already had a transcript file; `--session-id` is for creating a specific new session, while `--resume <id>` continues an existing one. A less common Windows cause is a stale headless Claude process that still owns the backing session after a crash or duplicate restart. Current dashboard-managed Claude work is PTY/channel backed and no longer uses `claude -p`.

**Fix (current build).** Managed Claude runs detect this exact failure and stop instead of silently creating a fresh session. Silent session replacement discards native Claude chat memory, so it is now an explicit operator choice. Close the duplicate Claude process that owns the session, or use Dashboard **Sessions -> Actions -> Recreate** when you intentionally want the next run to start with a fresh backing session. Restart the Windows `aify-comms` bridge after updating so it loads the fixed runtime adapter.

**Resume behavior.** Current bridge builds start interactive Claude Code through the managed PTY/channel path and pass `--resume <session-id>` when a saved handle exists. Pull latest, rerun the installer, and restart the Windows `aify-comms` bridge so it loads that path.

**Hidden-process caveat.** The duplicate owner may still be an old headless managed `claude -p` child, not a visible CLI tab. Older bridge builds on Windows launched Claude through `cmd.exe /c`; killing or superseding the bridge could kill `cmd.exe` without killing the Claude child, leaving a stale process behind. Current bridge code terminates the whole process tree on timeout, stop, interrupt, and bridge shutdown. When a managed Claude run still hits the error after the transcript/resume check, the Windows bridge first looks for a process command line containing the exact locked session ID, excludes interactive `claude-aify` / `--resume` commands, kills that process tree, and retries once. If the session ID is not visible in the process command line, it also checks aify runtime markers for the same workspace and stops a marked Claude parent only when that parent looks headless (`-p`, `--print`, or `--session-id`).

If the automatic cleanup cannot find a matching headless process, remove the stale Windows Claude process manually. From an elevated PowerShell:

```powershell
Get-CimInstance Win32_Process |
  Where-Object {
    $_.CommandLine -match 'b67ab2d1-a121-43d2-9c63-5ad0a2883e72' -and
    $_.CommandLine -notmatch 'claude-aify' -and
    $_.CommandLine -notmatch '(^|\s)--resume(\s|=)'
  } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Replace the session ID with the one from the run error. If that finds nothing, list likely hidden Claude owners for the workspace with:

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'claude|claude-channel|aify-comms' } |
  Select-Object ProcessId,ParentProcessId,Name,CommandLine |
  Format-List
```

Then restart the Windows `aify-comms` bridge and recover/restart the dashboard session. Use **Recreate** only when you accept losing that native Claude memory.

**Visibility caveat.** Dashboard-managed Claude Code is now a managed `claude-aify` PTY backing. Browser Console can attach to that PTY, and a separate native CLI can still be opened with the dashboard's copyable resume command (`claude-aify --resume <session-id>`) after the backing has recorded a resume ID.

If you want the resumed CLI to match managed-agent permissions, use `--dangerously-skip-permissions`. Do not use `--permanently-skip-permissions`; Claude Code rejects it as an unknown option.

Prefer the dashboard resume command or `claude-aify --aify-agent <agentId> --resume <session-id>` when opening a managed Claude session directly. The wrapper auto-registers a resident candidate, but ownership does not move automatically. Use dashboard **Switch to resident** when the visible CLI should own delivery, and **Switch to managed** when dashboard sends should return to the managed backing. **Pause for CLI** remains an explicit safety control when you want dashboard sends to fail fast while the terminal owns the session.

After opening the native CLI, re-register from that same session with the same `agentId`. That is how the dashboard learns the current native handle. If the agent forgets CLI conversation after returning to dashboard, check whether the session's stored handle changed or was recreated during adopt/restart. Current code should preserve handles across same-runtime adopt/recover/restart; a new handle should only appear after a new spawn or explicit **Recreate**.

**Dashboard handle repair.** If you know the correct native Claude session ID / Codex thread ID / OpenCode or Pi handle, use Dashboard **Chat details -> Runtime Session -> Set handle** or **Sessions -> Actions -> Set handle**. This updates the identity's saved `sessionHandle`, runtime state (`sessionId` or `threadId`), and latest session record without creating a fresh context. Use it only when you know the handle belongs to the intended transcript/thread; a wrong handle binds the identity to the wrong native memory.

**Resident caveat.** Resident Claude sessions are not silently swapped, because their session ID is the visible CLI binding. If a resident session hits this, close the duplicate Claude tab/process, restart with `claude-aify`, and re-register from the live session.

## Managed spawned agent workspace is stored as `\home\dev\...`

**Symptom.** A Linux/macOS/WSL managed spawn shows a workspace like `\home\dev\projects\repo` instead of `/home/dev/projects/repo`.

**Cause.** Older service builds normalized slash style for root validation but persisted the original requested workspace string into spawn/session records.

**Fix.** Current service builds normalize workspace paths for the selected environment before persisting spawn requests and runtime specs. Non-Windows environments store POSIX slashes; Windows environments keep Windows path style. Update/restart the service, then retry or repair the affected spawn/session workspace.

## Claude/Pi managed run fails: `spawn "/path/to/claude-or-omp" ENOENT`

**Symptom.** A managed Claude Code or Oh My Pi run fails before the agent replies with an error like `spawn "/home/dev/.local/bin/claude" ENOENT` or `spawn "/home/dev/.local/bin/omp" ENOENT`, even though the diagnostic says `command -v` resolved the launcher.

**Cause.** Node reports the same `spawn <command> ENOENT` shape when either the command cannot be executed **or the requested runtime cwd/workspace does not exist on that bridge host**. This is common after moving between Windows, WSL, Linux, or another PC: the launcher path may be valid, but the saved agent workspace belongs to a different environment or a root that is not mounted there. Other real launcher causes are still possible: missing execute bit, stale symlink, a script with a broken shebang interpreter, or an ELF/native binary whose loader is missing.

**Fix (current build).** Runtime launches preflight the cwd before `spawn()`. A bad workspace now fails as `Workspace "... " does not exist on this bridge host` / `not a directory` / `not readable`, instead of blaming `claude` or `omp`. Update and restart the host-side bridge/wrapper so this diagnostic is loaded.

If the cwd is valid and the error still says the launcher cannot execute, verify the launcher on the same host/user as the bridge:

```bash
ls -l /home/dev/.local/bin/claude /home/dev/.local/bin/omp
readlink -f /home/dev/.local/bin/claude /home/dev/.local/bin/omp
head -1 /home/dev/.local/bin/claude /home/dev/.local/bin/omp
command -v node bun claude omp
```

Set `AIFY_CLAUDE_COMMAND` or `AIFY_PI_COMMAND` only when you know the absolute path points at a real executable for that host. If the problem is a workspace mismatch, repair the agent/session workspace or spawn/adopt it in the environment that owns that path; do not paper over it with a launcher override.

**If the error still shows an old `bridge build=` after an update.** That is not Node's module cache. The build tag is computed from the git checkout that the currently running bridge process loaded. If it says `bridge build=231c607...` after the repo has newer commits, the active process was not restarted or is running from a different checkout. On the affected host, inspect the PID from the error:

```bash
ps -fp <pid>
readlink -f /proc/<pid>/cwd
tr '\0' '\n' < /proc/<pid>/environ | grep -E '^(AIFY|PATH|HOME)='
cd /home/dev/aify-comms && git rev-parse --short HEAD
```

Then stop every old bridge/wrapper for that host and start a fresh bridge from the intended checkout:

```bash
pkill -f 'mcp/stdio/server.js'
pkill -f 'aify-comms'
pkill -f 'claude-aify'
pkill -f 'omp-aify'
cd /home/dev/aify-comms
git pull
bash install.sh --client codex http://192.168.100.10:8800 --with-hook
bash install.sh --client claude http://192.168.100.10:8800 --with-hook
# Pi/OMP wrapper install is disabled; managed Pi uses the environment bridge plus `omp --mode rpc`.
aify-comms /path/to/workspace-root
```

The next dashboard failure/success diagnostic should report the new build tag. If it does not, the dashboard is still talking to another bridge process or another checkout.

## Machine ID shows `win32:unknown-host`

**Symptom.** Agent's `machineId` is `win32:unknown-host` instead of the real hostname.

**Cause.** `COMPUTERNAME` / `HOSTNAME` env vars were not propagated into the node process that hosts the bridge. The current build falls back to `os.hostname()` before `unknown-host`.

**Fix.** Restart the bridge or wrapper session and re-register. Cosmetic only — it does not block routing, because dispatches are routed by `agentId` rather than `machineId`.

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

**Fix.** Update/rebuild the service. Current builds only use PTY-input delivery when `insert_messages_via_console=true`; otherwise `/dispatch` persists `execution_mode='managed'` for native managed runtimes and the bridge controller claims it. For wrapper-backed Codex/Hermes, the wrapper child bridge claims `execution_mode='channel'`; the environment bridge is blocked from claiming those runs directly.

**Claude note.** Managed Claude still needs a `claude-aify` PTY host, but default delivery is channel notification (`insert_messages_via_console=false`), not raw stdin typing. If Claude channel runs sit queued, check that the wrapper PTY is running and the env advertises `claude-code` terminal support.

## Managed claude dispatch cancelled with "capabilities do not include managed-run"

**Symptom.** Send to a managed claude-code agent with `insert_messages_via_console=false (the default channel-route mode; earlier name claude_managed_channel_only=false)`. The dispatch_event row reads `agent capabilities do not include "managed-run"` and the run is cancelled before delivery. The agent's capabilities show `["resume", "interrupt", "spawn"]` (no `managed-run`) and `runtime_config.channelEnabled=true`.

**Cause.** Default capabilities for managed claude omit `managed-run` by design (claude has no headless managed-run API). Pre-`a4498a6`, `_agent_execution_mode` rejected dispatches on the missing cap before the channel branch could fire.

**Fix.** Already fixed in `a4498a6` — the cap-check is skipped when runtime is `_CHANNEL_MANAGED_RUNTIMES` AND `runtime_config.channelEnabled=true`. Container needs rebuild to pick up the api_v2.py change. Do not add `managed-run` by hand in the database; that can mask the real channel/wrapper health problem.

## Spawn-time initial message to managed claude sits queued forever

**Symptom.** `comms_spawn(runtime="claude-code")` registers the agent + spawns wrapper PTY successfully, but the spawn's `initialMessage` dispatch_run has `status='queued'` and `execution_mode='managed'` — never claimed by `claude-channel.js`. With `insert_messages_via_console=false (the default channel-route mode; earlier name claude_managed_channel_only=false)`, the run should have `execution_mode='channel'`.

**Cause.** Pre-`a4498a6`, `update_spawn_request`'s running-transition handler called `_create_dispatch_runs(...)` to create the initial-message run, but did NOT call `_apply_channel_only_to_claude_runs(...)` afterward. The run stayed `execution_mode='managed'` even with the channel-only setting on. The same gap existed in the auto-mirrored handoff path at line 4912.

**Fix.** Already fixed in `a4498a6` — both call sites now apply channel-only post-create. For runs created before the fix that are stuck queued, prefer cancelling/retrying after rebuilding and restarting the bridge/wrapper. Avoid manual `dispatch_runs` SQL unless you are doing a one-off forensic repair and have captured the original run state.

## Channel-routed claude dispatches stay queued forever (resident or managed)

**Symptom.** Send to a claude-code agent (resident OR managed). `dispatch_runs.status` stays `queued`, `execution_mode='channel'`. No `claimed` event, no delivery. The bridge appears alive (heartbeats), `aify-comms` MCP tools work for OTHER tasks, but channel dispatches sit forever.

**Cause.** Known Claude Code bug ([anthropics/claude-code#38462](https://github.com/anthropics/claude-code/issues/38462), [#21341](https://github.com/anthropics/claude-code/issues/21341)): when Claude loads many stdio MCP servers simultaneously, the slower ones get stuck in `still connecting` state. With a typical operator `~/.claude.json` (10+ servers including browsermcp, claude.ai connectors, etc.), `aify-comms-channel` loses the init race. Claude never registers the `notifications/claude/channel` listener, so the bridge's `mcp.notification()` calls are silently dropped — even though the MCP server itself is running and the channel-bridge reports `delivered`.

Verify by running `claude -p "list MCP servers"` from a plain shell — if `aify-comms-channel` shows under "still connecting" (instead of "connected"), the race is biting.

**Fix.** Set `AIFY_CLAUDE_STRICT_MCP=1` before launching `claude-aify` — it forces `--strict-mcp-config` with ONLY `aify-comms` + `aify-comms-channel`, which sidesteps the init race. (The default, flipped 2026-05-25, loads your full `~/.claude.json` MCP list — that's where the race comes from, but it means your other MCP servers ARE available in the wrapper.) Re-run `install.sh --client claude` if the wrapper is stale, then relaunch `claude-aify` with the env var set.

If you're on Windows Git Bash and the regenerated wrapper still fails (`2 MCP servers failed`, `aify-comms is currently disconnected`), the wrapper's MCP config paths may be MSYS-style. The wrapper uses `cygpath -m "$SCRIPT_DIR"` to convert to Windows-native paths. If cygpath isn't available in your Git Bash, install it (`pacman -S cygwin-tools` or update Git for Windows).

## Channel/resident dispatches silently fail on Windows — bridge can't reach localhost:8800

**Symptom.** Send to a resident or channel-routed claude-code agent. The dispatch_runs row stays `status='queued'` forever, `claim_bridge_id=''`. The channel-bridge child process is alive (verify with `Get-CimInstance Win32_Process | Where-Object CommandLine -match claude-channel.js`), `agent_turn_state.turn_busy=0`, the wrapper has the right flags (`--dangerously-load-development-channels`), and `~/.claude/projects/*/[session].jsonl | grep -c notifications/claude/channel` returns **0**. Nothing about the bridge looks wrong — but no claim ever happens and no `<channel source="aify-comms-channel">` event reaches the operator's session.

Smoke test that confirms the bug:
```bash
curl --max-time 5 http://localhost:8800/health                # times out
curl --max-time 5 http://127.0.0.1:8800/health                # returns immediately
node -e 'fetch("http://localhost:8800/health",{signal:AbortSignal.timeout(5000)}).then(r=>r.text()).then(console.log).catch(e=>console.log("ERR",e.message))'  # ERR aborted
node -e 'fetch("http://127.0.0.1:8800/health",{signal:AbortSignal.timeout(5000)}).then(r=>r.text()).then(console.log).catch(e=>console.log("ERR",e.message))'  # OK
```

**Cause.** Docker Desktop on Windows reports IPv6 port bindings (`docker port aify-comms-service` shows both `0.0.0.0:8800` and `[::]:8800`), but its IPv6 port forwarding to the container is unreliable — connections to `::1` hang silently. On Windows, `localhost` resolves to IPv6 `::1` first, so node's `fetch()` and curl both hit the broken IPv6 path. The channel bridge's `/dispatch/claim` poll aborts at `HTTP_TIMEOUT_MS` (20s) every cycle, no run is ever claimed, no `notifications/claude/channel` is ever emitted, and the symptom looks identical to a missing channel-server registration or a queue-routing bug. The same applies to managed runs going through `server.js` HTTP, and to anything else the wrappers do over `http://localhost:8800`.

**Fix (shipped, commit `71f2576`).** `claude-channel.js` and `mcp/stdio/server.js` now coerce `http://localhost` URLs to `http://127.0.0.1` before fetching. Defensive — works regardless of what env vars or wrappers pass. No-op on Linux/macOS (same loopback address). The wrapper template still uses `127.0.0.1` directly in the generated MCP config so operators on Linux don't notice anything; the bridge-level fix protects against custom `AIFY_SERVER_URL=http://localhost:...` configs and stale wrappers that predate the install regeneration. Run `install.sh --client claude` and restart `claude-aify` after pulling — verify with the smoke test above.

**Manual quick-fix while you wait to update.** Set `AIFY_SERVER_URL` / `CLAUDE_MCP_SERVER_URL` to `http://127.0.0.1:8800` in the wrapper's MCP env block (or `~/.claude/settings.local.json`) before launching the wrapper.

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

## Stale session handle causing prompt.submit failures (Plan 6 A)

**Symptom.** Dispatch fails at delivery time with `prompt.submit failed: session not found` (hermes) or analogous "session not found" / GC'd-rollout warnings on codex / pi / claude. Bridges look alive, heartbeating, and the dispatch row reports `delivered` — but the runtime rejects the handle. `agents.session_handle` matches a session that no longer exists in the runtime.

**⭐ ROOT CAUSE for managed HERMES + FIX (`9a71b72`, 2026-06-04) — the recurring one.** Hermes has TWO ids per session: a **durable `session_key`** (timestamp form `20260604_215845_395891`, persisted in the SessionDB, what `--resume`/`session.resume` REQUIRE) and an **ephemeral `sid`** (`uuid4().hex[:8]`, e.g. `8b821120` — the gateway's in-memory `_sessions` key, **regenerated on every gateway restart**). `session.active_list` rows carry both: row `id`/`session_id` = ephemeral, `session_key` = durable. The bridge was capturing/persisting the **ephemeral** id as the agent→session marker/handle (`rowRealId` read `r.id`; the TUI also writes the ephemeral id to the active file on a FRESH session), so the next launch did `hermes --tui --resume <ephemeral>` → the sid was gone → gateway **4007 "session not found"**, with **no resume-or-fresh fallback**. This regressed ~2026-06-03 when the native-session-id rework retired the always-resumable `aify-<id>` pre-seed and replaced it with capturing the wrong id. **Fix:** `resolve-session` now persists/resumes the **durable `session_key`** (new `rowResumeKey()` in `hermes-gateway-protocol.js`; delivery `prompt.submit`/`steer` still target the ephemeral sid — they're split), and on **no resumable session it returns "" (start fresh) + clears the dead marker** so a stale id stops being replayed by send-driven spawns. A poisoned marker (`8b821120`/`${...}`) now self-heals to a fresh session instead of erroring. **Deploy:** `./install.sh --client hermes` + relaunch. If you still see 4007 after that, the wrapper/native copy is pre-`9a71b72`.

**⭐ SECOND root cause — "FRESH session / lost history on EVERY restart" + FIX (`3a38d30`, 2026-06-04).** `resolve-session` decided what to resume from the gateway's live **`active_list`**, which is **EMPTY after any gateway/aify-comms restart** (sessions live in the SessionDB but aren't "loaded"). So the marker's real session was never found "live" → it fell to **fresh**, and the `9a71b72` fresh-fallback then **cleared the marker** → the agent abandoned its history and minted a brand-new session every launch. Verified live: a fresh gateway reports `active_list`=0 while `session.list`=69 (incl. the agent's real, resumable session). **Fix:** `resolve-session` now also queries **`session.list` (the SessionDB)** and PREFERS a marker that is **resumable-from-DB** (stable across restarts); it falls to `active_list`-most-recent only when there's no marker, and the dead-marker clear fires **only when the DB positively confirms the marker is gone** (`dbConsulted` guard — a transient `session.list` failure never clears a still-resumable marker). If an agent keeps coming up on a fresh/empty session after restarts, the wrapper/native copy is pre-`3a38d30` — `./install.sh --client hermes`. To restore a specific prior conversation, seed its durable `session_key` (from `hermes sessions list`, e.g. `aify-sc-coder #101` → `20260603_114935_8f7b7a`) into the marker / via dashboard Set handle.

**Detection.** Compare the stored handle against the runtime's actual current session id.

1. Get the stored handle from the server:
   ```bash
   curl -s http://localhost:8800/api/v1/agents/YOUR-AGENT-ID | python -m json.tool | grep -E '"sessionHandle"|"runtime"'
   ```

2. Get the runtime's actual current session id, per runtime:

   - **hermes**: do **not** use gateway `session.most_recent` as the current visible session — it can be historical DB state. The visible-TUI runs on the agent's **native hermes session id** (a normal timestamp id stored as the `sessionHandle`, symmetric with claude/codex) — there is no synthetic `aify-<agentId>` session. The PRIMARY id source is the per-agent active-session file (`HERMES_TUI_ACTIVE_SESSION_FILE` / `AIFY_HERMES_ACTIVE_SESSION_FILE`), bound to the agent by the `aify-hermes-session-<agentId>` marker. To find the live runtime sid, read that file (or ask the gateway `session.active_list` for the agent's stored real id), or just use `comms_agent_info`:
     ```bash
     curl -s http://localhost:8800/api/v1/agents/YOUR-AGENT-ID | python -m json.tool | grep -E '"sessionHandle"|"sessionId"'
     ```
   - **codex**: for a fresh `codex-aify`, do **not** scan `~/.codex/sessions`; the newest rollout may be an unrelated historical thread. Use `$CODEX_THREAD_ID` only if this exact session exported it, usually after `codex-aify --resume <id>`.
   - **pi**: `~/.omp/agent/sessions/<project-key>/...`, OR ask the bridge directly:
     ```bash
     curl -s http://localhost:8800/api/v1/agents/YOUR-AGENT-ID/pi-session-state | python -m json.tool
     ```
   - **claude**: list transcripts the operator's session might have created:
     ```bash
     ls -t ~/.claude/projects/*/*.jsonl | head -5
     ```
     If the stored `sessionHandle` (an `<id>.jsonl` basename) doesn't appear, it's stale.

3. If the runtime's truthful id differs from `agents.session_handle`, this is the Plan 6 A gap.

**Fix.**
- With Plan 6 A1 in place (`mcp/stdio/session-handle-heartbeat.js:25-30`, commit `3167423`) the bridge auto-corrects within one heartbeat tick (~60s). Wait 60s and re-check the stored handle — it should now match the runtime.
- With Plan 6 A2 in place (`mcp/stdio/server.js` `computeInitialSessionHandle`, commit `edbc374`) the FIRST register call also uses discover over env — fresh agents are correct on first dispatch.
- If you're running pre-Plan-6 bridge code (verify with `git log mcp/stdio/session-handle-heartbeat.js | head -3` showing the Plan 6 A1 commit), pull + restart the wrapper.
- One-shot manual recovery without waiting for the heartbeat: re-register the agent with an empty `sessionHandle` and let the bridge's discover fill it:
  ```
  comms_register(agentId="YOUR-AGENT-ID", role="...", runtime="...", cwd="...", sessionHandle="")
  ```
  OR unset the runtime's session env var in your shell before relaunching the wrapper:
  ```bash
  unset HERMES_SESSION_ID    # hermes
  unset CODEX_THREAD_ID      # codex
  unset PI_SESSION_ID        # pi
  unset CLAUDE_SESSION_ID    # claude
  hermes-aify --aify-agent YOUR-AGENT-ID   # or codex-aify / pi-aify / claude-aify
  ```
  Current `codex-aify` and `hermes-aify` deliberately do not rediscover from historical runtime state on fresh launch; explicit `--resume <id>` is the only wrapper-side handle export. For Hermes, a fresh visible session becomes wakeable after the TUI writes the active-session file and the live bridge registers/heartbeats it.

## Fixed check: wrapper-backed channel claim must be child-owned

**Symptom.** A wrapper-backed Codex/Hermes dispatch is routed through `executionModes=["channel","resident"]`, but the environment bridge claims the run before the bridge-spawned wrapper child is fully registered. The dashboard may show the run as claimed/running while the visible wrapper terminal never receives the message.

**Cause.** Old builds allowed a non-wrapper child bridge to claim wrapper-backed channel work. That bridge lacks the local app-server/gateway context and can only fail or fork hidden work.

**Fix.** Current builds require `bridge_kind='managed-wrapper-child'` and the current active wrapper `terminal_id` before a wrapper-backed Codex/Hermes child can claim channel work. If you see this symptom, rebuild/redeploy the service, restart the environment bridge, then recover/restart the managed session so a fresh wrapper child registers.

## Managed claude freezes on boot at a prompt (resume / compaction / permissions)

**Symptom.** A freshly-spawned or restarted managed claude sits at an unanswered TUI prompt
(the "Resume from summary / Resume full session" menu, a compaction question, the bypass-
permissions accept, or a channel-enter prompt) and never reaches a usable turn.

**Fix (2026-06-05).** The host bridge auto-answers these via a centralized rules layer
(`claude-console-prompts.js`): resume → **full session** (↓+Enter), the rest → Enter. Gated
to **managed claude only** (never a resident/operator session), requires an interactive menu
cursor (`❯`) and that claude is NOT mid-turn, fires once per appearance. If a NEW prompt
appears after a claude update, capture the frame into `mcp/stdio/tests/fixtures/claude-console/`
and add a rule. Kill-switch: `AIFY_NO_AUTO_ANSWER=1` (set in the wrapper env) disables it.

**Hardened (2026-06-12, `aca7562`) — the silent auto-compact-on-resume.** The channel-enter
rule once matched the bare substring `development-channels`, which also appears in the
worker's own BOOT OUTPUT (`--dangerously-load-development-channels …`) — at the moment the
resume menu rendered, the blind Enter accepted the highlighted "Resume from summary
(recommended)" and silently summarized the session away on EVERY cold start (operator: "it
auto compacts each time"). Now: channel-enter matches only the dialog's own question line
(`Enter channel to receive …`); any visible resume-menu text suppresses ALL blind-Enter rules
until the cursor-aware resume rule can answer; matching is recency-first (the latest dialog
text in the stream wins, so a scrolled-away menu can never re-claim a live dialog). If a
managed claude still loses context on restart, its PTY-hosting environment bridge predates
this fix — restart the `aify-comms` wrapper.

## Resident relaunch goes offline + deaf (auto-register refused by the race guard)

**Symptom.** Close a resident wrapper and relaunch it quickly. The session works for
SENDING, but: status reads `offline`, inbound messages never arrive (runs queue/fail with
no claimer), the sidecar bridge row stops heartbeating at the relaunch moment, and the
bridge boot log shows `auto-register for "<agent>" was refused — another live wrapper
owns this session (HTTP 409 … seen Ns ago)`.

**Cause (fixed 2026-06-13).** Kill-prior kills the old session seconds before the new
bridge boots, but the dead bridge's heartbeat lease (~150s) makes it look like a LIVE
owner — the Phase-4 race guard 409'd the auto-register, which never retried. No binding
file → `claude-channel.js` never binds (mute: no claims, no liveness) and
`runtime_state.bridgeInstanceId` stays pinned to the dead bridge (→ `offline`).
**Fix:** the server now allows a SAME-session-handle relaunch to take over an IDLE
prior bridge (supersedes it; a prior with an in-flight claimed/running run still 409s —
the Phase-4 in-flight protection stands), and the bridge retries a refused auto-register
every 30s for ~4 min. **Recovery on an old bridge:** run `comms_register` inside the
session (binds immediately, no restart) or relaunch once more after updating.

## Resident sends say "sent" but the agent never receives them (post mode-switch)

**Symptom.** An agent was switched managed→resident (operator launched the resident
terminal FIRST, clicked "switch to resident" SECOND). Sends report "sent", runs sit
`queued` with `claim_bridge_id=''`, and the agent's `channel-…` sidecar bridge row stops
heartbeating at the exact switch moment.

**Cause (fixed `9d81ea8`, 2026-06-12).** The switch clobbered `driver_state` to 'idle',
so the server answered the resident session's OWN channel sidecar with the mode-FSM
`release` — and the sidecar permanently exited its poll loop. Fix: the switch keeps
'driving' when adopting a live resident candidate; the release sites self-heal (a fresh
resident bridge ⇒ adopt driving, never release); claude-channel.js treats `release` as a
60s dormant re-check instead of a permanent stop. **Recovery on an old bridge:** restart
the agent's terminal — queued runs deliver as soon as a live sidecar claims.

## Send to an `available` managed claude FAILED after ~180s instead of cold-starting

**Cause (fixed `9d81ea8`, 2026-06-12 — root-cause-G parity).** The channel-mode claude
branch never fell back to `_coldstart_spawn_request_for_dispatch` when
`_ensure_managed_pty_for_dispatch` had no usable session row (the post-env-restart
state); the run sat queued with a claimer that could never exist until the queued-run
backstop failed it. hermes/codex always had the fallback; claude now does too
(`test_dispatch_claude_coldstart.py`). Re-send after deploying — the message cold-starts
a worker.

## Run failed with a "provider rate-limiting, not your request — retry shortly" notice

**Symptom.** A dispatch run you sent comes back FAILED and the sender notice says something like
*"provider rate-limiting, not your request — retry shortly"* rather than a raw API error.

**This is expected, not an aify bug.** As of 2026-06-07 (`11e7a5a`), when a run fails because the
underlying provider throttled the worker (an Anthropic "temporarily limiting requests" / "hit your
limit" message, an HTTP 429/529, or an "overloaded" error), the failure mirrored back to the SENDER
is rewritten into a clear, human notice instead of surfacing the raw provider/API error text. It
means: the request was fine, the provider is rate-limiting the model right now, and you should
**retry shortly**. The agent itself is healthy.

**What to do.** Wait a short while and re-send — there is nothing to repair on the aify side. If the
notice persists across many minutes, the provider throttle is sustained (check the agent's Console
for the upstream provider message), but the run-failure path is working as designed.

1. Capture the exact symptom (dispatch run ID, agent ID, error text).
2. Hit `curl http://localhost:8800/api/v1/dispatch/runs/<id>` to get the raw run state.
3. Hit `curl http://localhost:8800/api/v1/agents/<id>` for the agent state.
4. Forward those three pieces to whoever is debugging aify-comms. A fresh repro against current code (post-hard-reset) is worth 10× more than a trace against stale state.

## Bridge log lines: `claim timed out` / `503 database is locked` / `fetch failed` — triage (2026-07-01)

Three different signatures, three different meanings — don't conflate them:

- **`fetch failed` / `transient HTTP error … will retry on next poll` … `recovered after N failure(s)`** — TCP-level "service momentarily unreachable," almost always a service container restart (a deploy) or a brief network blip. The bridge retries and self-heals; the `recovered after N` line confirms it. **Ignore it** unless it does NOT recover (many consecutive with no `recovered`), which means the service is actually down — check the container.
- **`HTTP 503 … database is locked`** — write-lock contention under load. As of `d069f51` the service RETRIES the write (3×, 0.1/0.25/0.5s backoff) before ever surfacing a 503, so this should be rare; if it appears it's genuine sustained overload (correct backpressure), not a transient. The claim endpoints never 503 on contention — they return an empty claim (200) and retry next poll (`6eb3263`).
- **`claim … timed out after 28000ms`** — the old long-poll lock-overshoot, FIXED (`6eb3263`): claim probes now open with a short busy_timeout (`SQLITE_CLAIM_BUSY_TIMEOUT_MS=1200`) and fail fast, and `longpoll.MAX_WAIT_S` is 25s (below the bridge's 28s HTTP timeout). If you still see it, the host's service predates the fix — `git pull && docker compose up -d --build`.

All three are server-side; a host running its own service must pull + rebuild to get the fixes. See DECISIONS.md, "Claim probes fast-fail; writes retry the lock before 503."
