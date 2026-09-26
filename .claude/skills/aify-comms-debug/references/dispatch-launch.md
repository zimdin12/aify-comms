# aify-comms debug: Managed launch, workspace, and host/runtime resolution

## A managed spawn or dispatch failed and you do not know why: read the dead worker's console

**Symptom.** A managed worker never came up, and the error names a category rather than a cause
(e.g. `no online environment can host it`).

**Do this first.** `comms_console_tail(agentId="<the agent>")`. It works without a live console:
with the worker gone it returns the worker's last recorded output marked `NOT LIVE`, cause line
first. Example: a hermes worker whose console held
`[hermes-managed-host] fatal: hermes dashboard ... did not become ready within 60000ms` — a hermes
install with no built web UI.

`NOT LIVE` is a dead worker's output, not a running session. `nothing was recorded` means it died
before printing; read the spawn request's `error`, then `aify-env doctor`. Once the named cause is
fixed, an ordinary `comms_send` cold-starts a fresh worker.

## Managed spawn never starts: aify-env is the spawner

aify-env is the only thing that starts managed workers; there is no local fallback. A down or
stale aify-env, a launcher without a `HARNESS_WRAPPER_VERSION` marker, or a spawn row with no argv
all surface here rather than as a runtime problem.

**Read first**, neither starts anything:
- `aify-comms doctor` → `spawn-delegation` (is the aify-env serving this host answering),
  `spawn-queue` (a claimed spawn that never started), `tier-version` and `env-code-currency`.
- `aify-env doctor` for the host itself.

**Fix.**
- aify-env down, unreachable or stale → tell the operator. Starting or restarting aify-env is
  theirs: a second one supersedes the first and reaps its managed workers.
- Launcher refused for a missing marker → re-run `bash install.sh --client <runtime>` so the
  launcher is rendered with it.
- Runtime shows unavailable → aify-env offers a runtime only when `claude` / `codex` / `hermes`
  resolves on the PATH aify-env was started with; the reason reads `<command> not found on PATH`.
  Fixing that PATH is the operator's step.

## Worker "launches then dies" during a slow SessionStart hook

**Symptom.** The terminal ends `reconciled_managed_ghost_console_dead_worker` and the Console's
last line is `Running SessionStart hooks…`.

A worker registers its claimer only after Claude finishes init, and a one-time plugin setup (an
`install-deps.js`, say) can hold SessionStart for minutes. The reaper treats streaming output as alive
(`MANAGED_ORPHAN_GRACE_SECONDS`), so leave a worker alone while its console still streams. Each
rapid restart kills the attempt still booting.

## Managed Claude run fails: `Session ID ... is already in use`

**Cause.** Another Claude process still holds that session: a duplicate tab, or a headless child
left by a crash. Nothing clears this automatically.

**Fix.** Close the holder, or use Dashboard **Sessions → Reset** (fresh context) when
you accept losing that native Claude memory. To find the holder on Windows (replace the id):

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match '<session-id>' -and $_.CommandLine -notmatch 'claude-aify' } |
  Select-Object ProcessId, ParentProcessId, CommandLine | Format-List
```

Stop only a process you have identified as the stale holder, never a live agent's wrapper. A
resident session is never swapped automatically: close the duplicate tab, relaunch with
`claude-aify --aify-agent <id> --resume <session-id>`, and re-register from it. If you know the
correct native handle, the agent's **Edit…** → *Native session handle* field repairs it without a
fresh context.

## Run fails: `spawn "<path>/claude" ENOENT` although the launcher resolves

Node's `spawn()` reports the same `ENOENT` when the **workspace** does not exist on that host; a runtime the
bridge launches says so directly (`Workspace "..." does not exist on this bridge host`). Repair the agent's workspace, or spawn it in the
environment that owns that path. If the workspace is valid, check the launcher on the same host and
user: `ls -l`, `readlink -f`, and `head -1` of it (a broken shebang also reads as ENOENT).

## An error still shows an old `bridge build=` after an update

The tag comes from the code the running process loaded: the native copy's `.aify-version` stamp,
or `.git/HEAD` for a checkout. An old tag means that process started before the install, or runs
from a different copy.

1. `aify-comms doctor`: `bridge-installed` red → re-run `bash install.sh --client <runtime>`;
   `bridge-current` names live bridges on old code.
2. Relaunch only your own agents, keeping their conversations:
   `<runtime>-aify --aify-agent <id> --resume <handle>`.
3. Managed agents are restarted from the dashboard (Sessions → Restart), one agent at a time.

The process patterns (`server.js`, `claude-aify`) match every agent on the host, including your own
session and aify-env's managed workers, so stop processes by id, never by pattern.

## Machine ID shows `win32:unknown-host`

The host process had no `COMPUTERNAME` / `HOSTNAME` and `os.hostname()` failed. Cosmetic: routing is
by `agentId`. Relaunch the wrapper and re-register.
