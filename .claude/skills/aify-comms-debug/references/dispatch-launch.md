# aify-comms debug: Managed launch, workspace, and host/runtime resolution

Split out of `dispatch-bridge.md` (2026-08-03) so one symptom does not load the whole catalogue. Sibling files are listed in the skill's routing table.

## Contents

- [Managed worker "launches then dies", stuck `available` — reaped mid-boot during a slow SessionStart hook](#managed-worker-launches-then-dies-stuck-available-reaped-mid-boot-during-a-slow-sessionstart-hook)
- [Claude managed run fails: `Session ID ... is already in use`](#claude-managed-run-fails-session-id-is-already-in-use)
- [Managed spawned agent workspace is stored as `\home\dev\...`](#managed-spawned-agent-workspace-is-stored-as-home-dev)
- [Claude/Pi managed run fails: `spawn "/path/to/claude-or-omp" ENOENT`](#claude-pi-managed-run-fails-spawn-path-to-claude-or-omp-enoent)
- [Machine ID shows `win32:unknown-host`](#machine-id-shows-win32-unknown-host)

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

## Claude managed run fails: `Session ID ... is already in use`

**Symptom.** A dashboard-managed Claude run fails immediately with an error like `Session ID e5b70d2b-b700-4b77-a6fe-d65ccb8f84c6 is already in use`.

**Cause.** For old data/old bridge builds, the common managed-run cause was using Claude Code's `--session-id` flag for a session that already had a transcript file; `--session-id` is for creating a specific new session, while `--resume <id>` continues an existing one. A less common Windows cause is a stale headless Claude process that still owns the backing session after a crash or duplicate restart. Current dashboard-managed Claude work is PTY/channel backed and no longer uses `claude -p`.

**Fix (current build).** Managed Claude runs detect this exact failure and stop instead of silently creating a fresh session. Silent session replacement discards native Claude chat memory, so it is now an explicit operator choice. Close the duplicate Claude process that owns the session, or use Dashboard **Sessions -> Actions -> Reset (fresh context)** when you intentionally want the next run to start with a fresh backing session. Restart the Windows `aify-comms` bridge after updating so it loads the fixed runtime adapter.

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

Then restart the Windows `aify-comms` bridge and restart the dashboard session. Use **Reset (fresh context)** only when you accept losing that native Claude memory.

**Visibility caveat.** Dashboard-managed Claude Code is now a managed `claude-aify` PTY backing. Browser Console can attach to that PTY, and a separate native CLI can still be opened with the dashboard's copyable resume command (`claude-aify --resume <session-id>`) after the backing has recorded a resume ID.

If you want the resumed CLI to match managed-agent permissions, use `--dangerously-skip-permissions`. Do not use `--permanently-skip-permissions`; Claude Code rejects it as an unknown option.

Prefer the dashboard resume command or `claude-aify --aify-agent <agentId> --resume <session-id>` when opening a managed Claude session directly. The wrapper auto-registers a resident candidate, but ownership does not move automatically. Use dashboard **Switch to resident** when the visible CLI should own delivery, and **Switch to managed** when dashboard sends should return to the managed backing. **Pause for CLI** remains an explicit safety control when you want dashboard sends to fail fast while the terminal owns the session.

After opening the native CLI, re-register from that same session with the same `agentId`. That is how the dashboard learns the current native handle. If the agent forgets CLI conversation after returning to dashboard, check whether the session's stored handle changed or was recreated during adopt/restart. Current code should preserve handles across same-runtime adopt/restart; a new handle should only appear after a new spawn or explicit **Reset (fresh context)**.

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
bash install.sh --client codex http://192.0.2.10:8800 --with-hook
bash install.sh --client claude http://192.0.2.10:8800 --with-hook
# Pi/OMP wrapper install is disabled; managed Pi uses the environment bridge plus `omp --mode rpc`.
aify-comms /path/to/workspace-root
```

The next dashboard failure/success diagnostic should report the new build tag. If it does not, the dashboard is still talking to another bridge process or another checkout.

## Machine ID shows `win32:unknown-host`

**Symptom.** Agent's `machineId` is `win32:unknown-host` instead of the real hostname.

**Cause.** `COMPUTERNAME` / `HOSTNAME` env vars were not propagated into the node process that hosts the bridge. The current build falls back to `os.hostname()` before `unknown-host`.

**Fix.** Restart the bridge or wrapper session and re-register. Cosmetic only — it does not block routing, because dispatches are routed by `agentId` rather than `machineId`.

