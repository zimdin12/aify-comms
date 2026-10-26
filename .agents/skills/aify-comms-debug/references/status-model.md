# aify-comms debug: Status: when the badge disagrees with reality, and what each state proves

## CHECK THIS FIRST: never `working` — the bridge has no agent id

**Symptom.** One agent stays `online` while clearly working. Messaging, registration and
heartbeats all look healthy, which is why this goes unnoticed for weeks.

**Cause.** The wrapper was launched without an agent id, so `AIFY_AGENT_ID` is missing from the
bridge's process. Every turn path is gated on it: the `Stop`, `UserPromptSubmit` and `PostToolUse`
hooks post nothing to aify-env, so aify-env never sees the turn.

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

## How the word is decided

Since 0.9.8 the word is aify-env's. Each host's aify-env publishes a record per agent; comms
mirrors it and serves the operational view (`service/api_core/operational_view.py`). First match
wins:

1. An agent no host defines, stopped by the operator → `stopped`.
2. A wake handle proven absent → `offline`.
3. Any publisher on the agent's machine not fresh (no snapshot within three of its announced
   intervals), unreadable, restored after a service restart, or incomplete → `unknown`, with the
   last known word and its time beside it. Two fresh records of one agent also read `unknown`.
4. Otherwise aify-env's state, promoted to `working` while a run comms delivered is running.

Only three words carry a note: an operator stop (their note), `misconfigured` (comms' reason: a
workspace outside the folder aify-env was started in, or a runtime it cannot launch) and
`unknown` (why). Delivery follows the same facts: a claim withholds on `unknown`, holds or
steers while aify-env says busy, and proceeds on known idle (`claim_decision`).

## The badge is wrong, or delivery stalls

- `unknown` on every agent of a host: comms lost its aify-env. `aify-comms doctor`
  `agent-state-publishers` names the publisher; `aify-env doctor` [host] says why. After a
  service restart every agent reads `unknown` until each aify-env's next snapshot (under a minute).
- A wrong word while its publisher is fresh: aify-env decided it. Compare the console
  (`comms_console_tail`) with `aify-env doctor` [host]; the screen rules are aify-env's.
- Queued work never delivers to an idle-looking agent: aify-env still says busy. Check the agent
  has an id (first entry above), then aify-env's record of its turn.
- `available` with a live Console, or a headless worker: aify-env's record of the worker;
  `_reconcile_managed_worker_hygiene` (`reconcilers/managed_workers.py`) reaps a headless
  orphan on the 60s loop.

## Status labels

Status is **proven, not time-assumed**: no state is inferred from elapsed time. The nine labels
below explain what each one proves; the action to take for each is the Status table in
[operations.md](../../aify-comms/references/operations.md). `service/contracts/vocabulary.json` is
the contract both tables follow.

| Label | Meaning |
|-------|---------|
| `working` | A turn is in progress (turn-start seen, no turn-end), or aify-env sees the worker generating on screen. Liveness-gated: a dead worker cannot hold `working`. |
| `shell` | Live worker idle at its prompt, background work: claude's footer `· N shell`, hermes' dock `N live agents`/`N procs` (read by aify-env). Delivers like `online`; never ends a held turn. |
| `online` | Live worker, no turn in progress. The ready state: queued work delivers to it. |
| `available` | Managed agent, host reachable, no live worker. The next send cold-starts one. |
| `blocked` | The turn is waiting on operator input (a prompt or question), not generating. Also while its quota pool is at its limit; the note says `resets by <time>`. |
| `offline` | The worker is gone and aify-env cannot start it here: a resident whose session ended, or a managed agent whose host cannot run it now. |
| `stopped` | Deliberately down: the operator disabled wake (`launch_mode='none'`), or a **resident** closed cleanly. A managed agent whose worker died is `available`, not `stopped`. |
| `starting` | A spawn is running and its worker has not appeared yet. Leave it alone: a restart now kills the boot. A send queues until the worker arrives. |
| `misconfigured` | The identity can never start (no spawn spec or host, no wake path, unknown runtime). A human must fix the config; sending will not help. |

## What feeds aify-env's turn signal

- **claude:** `UserPromptSubmit` and `PostToolUse` hooks post a turn start to aify-env; `Stop` posts the
  end through `claude-stop-gate.js`, which suppresses a premature mid-turn Stop and posts on any doubt.
  Nothing posts turns to comms since 0.9.9.
- **codex:** hooks.
- **hermes:** `hermes-gateway-turn-detector.js` reads the gateway session status; idle must hold for
  several consecutive reads before it clears, because hermes' running flag blinks off between tool
  calls. It still posts to comms, which ignores it until 0.9.10 moves hermes out of comms.

Every one of these paths needs `AIFY_AGENT_ID` in the bridge's process. An agent stuck `working`
or never `working` is usually that: see the first entry above.

## Exits and sessions

- A **resident** that exits cleanly POSTs `/agents/{id}/resident-lost` and rests `offline`: closing
  its own CLI is not an operator stop, so a stop already on its row is the only `stopped` kept.
- A **managed** agent reaching that endpoint (its hermes gateway died, for example) rests
  cold-startable at `available`, so the next send spawns a fresh worker. Ownership never switches
  between managed and resident by itself.
- Session badges are derived too (`_compute_session_display_status`), from the live terminal row
  (managed) or a fresh non-superseded bridge (resident). The stored session status is a cache.
- `delivered` proves a bridge accepted the dispatch, not that the model ran. For a load-bearing
  check, require the linked reply and read `comms_console_tail` when it is missing.
