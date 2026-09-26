# aify-comms debug: The status model: labels, derive(), and what each state proves

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
  (`TURN_BUSY_BACKSTOP_SECONDS`); one a live bridge owns renews, capped at 4 h
  (`TURN_LEASE_ABSOLUTE_MAX_SECONDS`).
- **Console lease:** `service/api_core/console_working.py` stamps a 20s lease when the rendered
  managed-claude screen shows the running footer. It holds a long turn at `working` when no fresh
  aify-env observation is available.

Every one of these paths needs `AIFY_AGENT_ID` in the bridge's process. An agent stuck `working`
or never `working` is usually that: see the first entry in status-symptoms.md.

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
