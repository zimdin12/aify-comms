# aify-comms debug: Status: when the badge disagrees with reality, and what each state proves

## CHECK THIS FIRST: `working` forever, or never `working` — the bridge has no agent id

**Symptom.** One agent stays `working` while idle and never self-heals, or (after a manual
`/turn-end`) stays `online` while clearly working. Messaging, registration and heartbeats all look
healthy, which is why this goes unnoticed for weeks.

**Cause.** The wrapper was launched without an agent id, so `AIFY_AGENT_ID` is missing from the
bridge's process. Every turn path is gated on it: the bridge turn detector never arms, and the
`Stop`, `UserPromptSubmit` and `PostToolUse` hooks do nothing. The channel sidecar carries the id
in its own config, so it still marks the turn busy (heartbeat `turnBusy`) on a wake and nothing
ever clears it.

**Diagnose [host].** The database cannot show this; read the process environment. On Linux
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

**Fix [host].** Relaunch with an identity from the agent's terminal; `--resume` keeps the conversation:

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
  heartbeat (not deliverable)". Restart it with `comms_restart(agentId)`; a resident is relaunched
  from its own terminal.
- A live claimer with no console is a headless orphan. It reads `available` and
  `_reconcile_managed_worker_hygiene` (`reconcilers/managed_workers.py`) reaps it on the 60s loop.
- `offline` on every managed agent of one host: its environment is unreachable. Check
  `aify-comms doctor` `env-bridge` and `aify-env doctor` [host]; restarting aify-env is [operator].

## Agent reads idle but its queued work never delivers (deaf agent)

**Symptom.** `online`/`available`, bridge alive, yet queued runs stay `queued`. A target without
the `steer` capability gets no run at all, so it can look deaf to every dispatch.

**Cause.** The delivery gates read the raw `agent_turn_state.turn_busy` flag, and only a turn-end
clears it. `_clear_turn_busy_for_dead_bridges` skips hook-driven resident-claude turns
(`turn_bridge_id='user-prompt-submit'`), and a killed harness or failed `Stop` hook leaves the flag
set. `_turn_busy_holds_delivery` bounds it by `TURN_BUSY_BACKSTOP_SECONDS` (30 min), the same
ceiling status uses, so it self-heals at 30 minutes.

**Triage [host].**

```bash
docker exec aify-comms-service python -c "
import sqlite3, glob
c = sqlite3.connect(sorted(glob.glob('/data/*.db'))[-1])
for r in c.execute('SELECT agent_id, turn_busy, turn_bridge_id, turn_updated_at FROM agent_turn_state WHERE turn_busy = 1'):
    print(r)
"
```

A row minutes old on a visibly idle agent is a latched flag. If it recurs for the same agent, fix
that runtime's turn-end signal ("What feeds the turn signal" below), not the gate.

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

## Status labels

Status is **proven, not time-assumed**: no state is inferred from elapsed time. The nine labels
below explain what each one proves; the action to take for each is the Status table in
[operations.md](../../aify-comms/references/operations.md). `service/contracts/vocabulary.json` is
the contract both tables follow.

| Label | Meaning |
|-------|---------|
| `working` | A turn is in progress (turn-start seen, no turn-end), or aify-env's screen observation shows the worker generating. Liveness-gated: a dead worker cannot hold `working`. |
| `shell` | Live worker idle at its prompt with background shells still running (claude's footer `· N shell`, seen by aify-env). Deliverable exactly like `online`; never ends a held turn. |
| `online` | Live worker, no turn in progress. The ready state: queued work delivers to it. |
| `available` | Managed agent, host reachable, no live worker. The next send cold-starts one. |
| `blocked` | The turn is waiting on operator input (a prompt or question), not generating. Liveness-gated like `working`. |
| `offline` | Heartbeat gone (`agent_liveness_seconds`, default 90s), a resident whose bridge lease lapsed (`resident_lease_seconds`, default 150s), or a managed agent whose host environment is unreachable. |
| `stopped` | Deliberately down: the operator disabled wake (`launch_mode='none'`), or a **resident** closed cleanly. A managed agent whose worker died is `available`, not `stopped`. |
| `starting` | A spawn is running and its worker has not appeared yet. Leave it alone: a restart now kills the boot. A send queues until the worker arrives. Bounded by the spawn window, after which it falls back to `available`. |
| `misconfigured` | The identity can never start (no spawn spec or host, no wake path, unknown runtime). A human must fix the config; sending will not help. |

## How `derive()` decides

`service/status_engine.py` `derive()` is the only status authority, a pure function of
`StatusInputs`. First match wins:

1. disabled → `stopped`; managed with its environment unreachable → `offline`.
2. Managed, worker present, and a **fresh aify-env screen observation** (`host_activity.py`,
   `HOST_ACTIVITY_FRESH_SECONDS`): working / blocked / shell / idle→`online`. An idle screen never
   ends a turn the service still holds.
3. In a turn and live → `working`, or `blocked` when the console awaits input.
4. Managed: alive with a worker → `online`; host reachable → `misconfigured`, `online` (console
   booting), `starting`, else `available`; otherwise `offline`.
5. Resident: alive, live session, fresh bridge → `online`; else `misconfigured` or `offline`.

## What feeds the turn signal

- **claude:** `UserPromptSubmit` and `PostToolUse` hooks POST `/turn-start`; `Stop` POSTs
  `/turn-end` through `claude-stop-gate.js`, which suppresses a premature mid-turn Stop and posts
  `/turn-end` on any doubt. The bridge transcript detector (`claude-turn-end-detector.js`) covers
  channel-woken and scheduled turns that fire no hook, and re-asserts the proven direction every
  45s (in-flight → `/turn-start`, ended → `/turn-end`).
- **codex:** hooks plus the app-server `turn/started` / `turn/completed` events.
- **hermes:** `hermes-gateway-turn-detector.js` reads the gateway session status; idle must hold for
  several consecutive reads before it clears, because hermes' running flag blinks off between tool
  calls. It runs for managed hermes and for any resident with `AIFY_HERMES_GATEWAY_URL` set.
- **Backstop:** a turn with no end-event ages out 30 min after it began
  (`TURN_BUSY_BACKSTOP_SECONDS`); one a live bridge owns lasts while its renewals keep coming.
- **Console lease:** `service/api_core/console_working.py` stamps a 20s lease when the rendered
  managed-claude screen shows the running footer. It holds a long turn at `working` when no fresh
  aify-env observation is available.

Every one of these paths needs `AIFY_AGENT_ID` in the bridge's process. An agent stuck `working`
or never `working` is usually that: see the first entry above.

**`turn_busy` (delivery gate) and `in_turn` (status) are decoupled on purpose.** A reply sent
mid-turn clears `turn_busy` so queued work can move, while `in_turn` clears only on a real turn-end.
`turn_busy=0` with `in_turn=1` means "replied, still working", not a bug.

## Exits and sessions

- A **resident** that exits cleanly POSTs `/agents/{id}/resident-lost` and reads `stopped` within
  seconds. A crashed resident reads `offline` once its lease lapses.
- A **managed** agent reaching that endpoint (its hermes gateway died, for example) rests
  cold-startable at `available`, so the next send spawns a fresh worker. Ownership never switches
  between managed and resident by itself.
- Session badges are derived too (`_compute_session_display_status`), from the live terminal row
  (managed) or a fresh non-superseded bridge (resident). The stored session status is a cache.
- `delivered` proves a bridge accepted the dispatch, not that the model ran. For a load-bearing
  check, require the linked reply and read `comms_console_tail` when it is missing.
