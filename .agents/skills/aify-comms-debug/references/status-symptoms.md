# aify-comms debug: Status symptoms: when the badge disagrees with reality

## CHECK THIS FIRST: `working` forever, or never `working` — the bridge has no agent id

**Symptom.** One agent stays `working` while idle and never self-heals, or (after a manual
`/turn-end`) stays `online` while clearly working. Messaging, registration and heartbeats all look
healthy, which is why this goes unnoticed for weeks.

**Cause.** The wrapper was launched without an agent id, so `AIFY_AGENT_ID` is missing from the
bridge's process. Every turn path is gated on it: the bridge turn detector never arms, and the
`Stop`, `UserPromptSubmit` and `PostToolUse` hooks do nothing. The channel sidecar carries the id
in its own config, so it still marks the turn busy (heartbeat `turnBusy`) on a wake and nothing
ever clears it.

**Diagnose.** The database cannot show this; read the process environment. On Linux
`aify-comms doctor` `agent-identity` does it for you, or:

```bash
# <ANONYMOUS> on a REGISTERED agent = this bug.
for p in $(pgrep -f "aify-comms/mcp/stdio/server.js"); do
  aid=$(tr '\0' '\n' < /proc/$p/environ | grep -m1 '^AIFY_AGENT_ID=' | cut -d= -f2)
  ppid=$(awk '{print $4}' /proc/$p/stat)
  echo "${aid:-<ANONYMOUS>}  pid=$p  cwd=$(readlink /proc/$ppid/cwd)"
done
```

On Windows that row skips. Corroborate instead: `aify-claude-session-<agent>.json` is missing from
the temp directory while healthy agents have one, and the wrapper printed
`NO AGENT ID: aify turn/status detection is DISABLED` at launch. A plain unregistered `claude`
session with the MCP attached is legitimately id-less and is not this bug.

**Fix.** Relaunch with an identity; `--resume` keeps the conversation:

```bash
claude-aify --aify-agent <agent-id> --resume <session-handle>
```

Re-registering does not help (`AIFY_AGENT_ID` is read once at bridge boot), and neither does
Claude Code's in-app `/resume` (same process, same env).

## Managed agent reads `online` but nothing delivers, or `available` with a live Console

`online` means deliverable: a live console **and** a live, non-superseded claimer (the
`claude-channel.js` sidecar for claude, the `hermes-managed-host.js` delivery loop for hermes, the
wrapper-child bridge for codex). The read-time gates `_enforce_live_worker_gate` and
`_enforce_env_reachable_gate` (`api_core/registration_gates.py`) enforce this.

- `available` with a live Console: the claimer is down, so the note reads "No live channel sidecar
  heartbeat (not deliverable)". Restart the agent from the dashboard; for a resident, relaunch its
  wrapper.
- A live claimer with no console is a headless orphan. It reads `available` and
  `_reconcile_managed_worker_hygiene` (`reconcilers/managed_workers.py`) reaps it on the 60s loop.
- `offline` on every managed agent of one host: its environment is unreachable. Check
  `aify-comms doctor` `env-bridge` and `aify-env doctor`; restarting aify-env is the operator's call.

## Agent reads idle but its queued work never delivers (deaf agent)

**Symptom.** `online`/`available`, bridge alive, yet queued runs stay `queued`. A target without
the `steer` capability gets no run at all, so it can look deaf to every dispatch.

**Cause.** The delivery gates read the raw `agent_turn_state.turn_busy` flag, and only a turn-end
clears it. `_clear_turn_busy_for_dead_bridges` skips hook-driven resident-claude turns
(`turn_bridge_id='user-prompt-submit'`), and a killed harness or failed `Stop` hook leaves the flag
set. `_turn_busy_holds_delivery` bounds it by `TURN_BUSY_BACKSTOP_SECONDS` (30 min), the same
ceiling status uses, so it self-heals at 30 minutes.

**Triage.**

```bash
docker exec aify-comms-service python -c "
import sqlite3, glob
c = sqlite3.connect(sorted(glob.glob('/data/*.db'))[-1])
for r in c.execute('SELECT agent_id, turn_busy, turn_bridge_id, turn_updated_at FROM agent_turn_state WHERE turn_busy = 1'):
    print(r)
"
```

A row minutes old on a visibly idle agent is a latched flag. If it recurs for the same agent, fix
that runtime's turn-end signal (status-model.md, "What feeds the turn signal"), not the gate.

## Managed claude's badge disagrees with its screen

For a managed agent, a fresh aify-env screen observation (`host_activity.py`) decides the status
ahead of the turn bookkeeping. Without one, the service's console lease (`console_working.py`,
20s, stamped from the rendered screen) holds a long turn at `working`.

- `online` while the Console shows the running footer: no fresh observation arrived. Check
  `aify-comms doctor` `tier-version` and `env-code-currency` (an older aify-env sends none), then
  whether the console streams at all (`comms_console_tail`).
- `blocked` while generating: `_agent_awaiting_input` (`api_core/liveness.py`) matched a claude
  permission, resume or compaction prompt on the rendered screen. Read the console tail to see
  whether a prompt is really up.
