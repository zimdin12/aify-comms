# aify-comms debug: Dispatch delivery: claims, steers and stuck runs

The `curl` examples read the service key from `$AIFY_API_KEY`; drop the header on a service
running without `API_KEY`.

## Dispatches stay `queued`, never claimed

**Symptom.** Sends to a claude/hermes agent sit `queued`; the agent looks `online`.

**Who may claim.** Managed claude: its `claude-channel.js` sidecar. Managed hermes: its
`hermes-managed-host.js run <agent>` loop (`bridge_kind='channel-sidecar'`). Wrapper-backed codex:
its `managed-wrapper-child` bridge. `_CHANNEL_CLAIM_RUNTIMES` in `service/api_core/channel_delivery.py`
lists the channel-claimable runtimes; `managed_via_wrapper` (default codex, hermes) picks which
runtimes deliver through their wrapper.

**Verify (read-only).**

```bash
docker exec aify-comms-service python -c "
import sqlite3, glob
c = sqlite3.connect(sorted(glob.glob('/data/*.db'))[-1])
aid = 'YOUR-AGENT-ID'
for r in c.execute(\"SELECT id, agent_id, bridge_kind, superseded_by, last_seen FROM bridge_instances WHERE agent_id=? ORDER BY last_seen DESC LIMIT 5\", (aid,)): print(r)
for r in c.execute(\"SELECT id, status, execution_mode, claim_bridge_id, created_at FROM dispatch_runs WHERE target_agent=? ORDER BY created_at DESC LIMIT 5\", (aid,)): print(r)
"
```

A healthy agent has a fresh, non-superseded claimer row. `queued`, `execution_mode='channel'`,
`claim_bridge_id=''` with no claimer row means the claimer is down: restart the agent from the
dashboard (managed) or relaunch its wrapper (resident). A resident claude refused with "no live
wake path" has no `runtime_config.channelEnabled=true`; relaunch it from a current `claude-aify`,
which exports `AIFY_CHANNELS_ENABLED=1`, rather than editing the database.

## Team stranded after a restart: runs stuck `claimed`

A run claimed by a bridge that then died is requeued by `_requeue_orphaned_claimed_runs` once it
is 90s old with no `delivered` event and no live claiming bridge, and a live claimer re-claims it.
A `queued` run whose target has no live claimer is failed after 180s by the queued-run backstop and
mirrored to the sender. Both run on the 60s reconcile loop, so waiting is the fix. For one agent
still stuck, restart that agent from the dashboard.

## Run stuck `running`, `comms_run_interrupt` has no effect

An interrupt is a control the owning bridge polls for; if that bridge died, nothing claims it.
When a replacement bridge polls, the server gives the old run a short grace
(`blockedBy.reason = "active_run_owned_by_previous_bridge"`), then fails it. The run summary then
reads `Auto-healed: bridge "<old>" replaced by "<new>"`; that is the recovery path, not data loss.
If it repeats on every dispatch, an old bridge is still polling and is refused with
`bridge_not_current`.

If no bridge is polling at all, cancel the run directly:

```bash
curl -X PATCH -H "X-API-Key: $AIFY_API_KEY" -H "Content-Type: application/json" \
  http://127.0.0.1:8800/api/v1/dispatch/runs/<run_id> \
  -d '{"status":"cancelled","error":"Bridge died, orphaned run"}'
```

Normal `comms_send` does not queue new work behind a blocked target; it returns a not-sent notice.

## `require_reply` run FAILED: "turn is presumed dead (model 429 / interrupt / stall)"

The worker's turn died without replying, so the run sat `delivered`. After
`stranded_reply_fail_minutes` (default 45) `_fail_stranded_delivered_reply_runs` fails it with the
cause and skips a run the agent is still working on. Re-send the ask; if the session is wedged,
restart the agent. Setting the minutes to 0 disables the backstop.

## Run failed: "provider rate-limiting, not your request — retry shortly"

The provider throttled the worker (429/529, "overloaded", "hit your limit") and the notice was
rewritten for the sender. Nothing to repair: wait and re-send. If it persists, read the agent's
Console for the provider's own message.

## A routine `delivered` run shows a blank summary

Intentional: successful deliveries carry no summary so the Runs view stays readable. Failures keep
theirs.

## `comms_send(steer=true)` stayed unread or queued behind itself

Current behaviour, per target: a live steer-capable active run turns the message into a steer
control, and the inbox copy is marked read when it completes. Busy but not steer-capable, the
message queues as next-turn work. A target that cannot take live delivery gets a not-sent notice.
Resident `claude-aify` steers through a `notifications/claude/channel` event. If you see otherwise,
capture `/api/v1/dispatch/runs/<id>` and `/api/v1/agents/<agent>`.

## `wakeMode: claude-needs-channel` although launched with `claude-aify`

The runtime marker is missing: the bridge writes it with its own pid under
`~/.local/state/aify-comms/runtime-markers/`, and a dead pid deletes it on the next read. Relaunch
through `claude-aify`, re-register from that session, and check that the marker persists with a
live pid:

```
comms_register(agentId="my-agent", role="coder", runtime="claude-code", cwd="C:/path/you/are/in")
comms_agent_info(agentId="my-agent")
```

## In-flight run cancelled: "bridge X is not the current agent bridge Y"

A sibling registration superseded the owning bridge. `_record_bridge_registration` supersedes only
on the full `(agent_id, machine_id, runtime, session_mode, session_handle)` tuple, and managed
launches carry `AIFY_SESSION_MODE=managed` (`service/api_core/launch_env.py`). Rows that differ in
`session_mode` or `session_handle` are the guard working.

## Managed claude run routed through the wrong path

- The first message after a spawn stays `execution_mode='managed'` and is never claimed:
  `_apply_channel_routing_to_claude_runs` routes new claude runs to `channel`, and
  `_reroute_orphaned_managed_channel_runs` moves a queued `managed` run to `channel` once the
  target has a live channel sidecar. Cancel and re-send one created before either ran.
- Dashboard text typed into a PTY instead of a run: only `insert_messages_via_console=true` (off
  by default) types messages into the console.
- Leave `managed-run` out of a claude agent's capabilities; claude delivers by channel, and adding
  it by hand hides the real claimer problem.

## Many `claude.exe --resume <same id>` for one agent

Kill-prior (`reap-managed-claude.js`) reaps only a `claude.exe` whose parent wrapper is
`claude-aify --aify-agent <thatAgent>`, so it never touches another agent or a resident session.
The session-collision guard parks a handle a different live agent already owns (`session-collision`
note). Remaining orphans are reported by `aify-comms doctor` (`managed-orphans`); cleaning them up is
the operator's call.

## Windows install notes

- Every wrapper bypasses permissions by default (`claude-aify` passes
  `--dangerously-skip-permissions`, `codex-aify` `--dangerously-bypass-approvals-and-sandbox`);
  `--safe` / `--no-auto` opts out per launch.
- `--with-hook` writes native Windows paths into Claude's `settings.json` and Codex's `hooks.json`,
  and the `.cmd` shims put Git's Unix tools on PATH.
- Check the install with `& "$env:USERPROFILE\.local\bin\aify-comms.cmd" doctor`; add
  `$env:USERPROFILE\.local\bin` to PATH if the command is not found.
- A runtime missing from the dashboard's launch list is one aify-env could not find:
  `claude` / `codex` / `hermes` must resolve on the PATH aify-env was started with. `aify-env
  doctor` shows it; fixing that PATH is the operator's step.
