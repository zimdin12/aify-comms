# aify-comms debug: Hermes sessions, gateway, ports and processes

## How a hermes agent is put together

One `hermes-aify` launch runs three processes per agent:

- **gateway host**: a hidden `hermes dashboard --port <P>` started by `ensureGatewayHost`
  (`hermes-gateway.mjs`). Its stderr goes to
  `~/.local/state/aify-comms/hermes-gateway-host-<port>.log`, truncated on each spawn.
- **delivery loop**: `hermes-managed-host.js run <agent>`, the agent's `channel-sidecar` claimer. It
  submits with `prompt.submit` when idle and `session.steer` when busy.
- **visible TUI**: `hermes --tui`, attached to that gateway through `HERMES_TUI_GATEWAY_URL`.

Each agent gets its own port, recorded in an `aify-hermes-port-<agent>` marker in the temp directory
(`resolveGatewayPort` skips ports other agents' markers claim). For any delivery question read
`comms_run_status(runId=...)` first, then the gateway-host log.

## Resident hermes send fails: `ECONNREFUSED 127.0.0.1:<port>`

The stored `runtimeConfig.gatewayUrl` points at a gateway that no longer runs, usually inherited
from the parent shell. The wrapper unsets inherited `HERMES_TUI_GATEWAY_URL` /
`AIFY_HERMES_GATEWAY_URL` before starting its own gateway, so relaunch `hermes-aify` (rerun
`bash install.sh --client hermes` first if the wrapper is old) and re-register from the visible TUI.
Two residents in one cwd must show different `gatewayUrl` values; if they match, re-register each
from its own terminal.

## Resident hermes reads `offline` with `wakeMode='hermes-missing-handle'`

The launch resolved no agent id (no `--aify-agent`, and no handle it could map back to one), so the
wrapper ran a plain TUI with no gateway and nothing can wake it. Relaunch with
`hermes-aify --aify-agent <id>` (add `--resume <key>` to keep the conversation). When an agent id is
known and its gateway fails instead, the wrapper exits with
`FATAL: managed gateway host for '<id>' did not come up`: see hermes-turns.md.

## Delivered messages never render in the visible TUI, or one agent splits into two sessions

The TUI started its own gateway instead of attaching to the loop's, because a stale
`HERMES_TUI_GATEWAY_URL` was set. Current wrappers unset it before exporting the live one; relaunch
`hermes-aify`. The loop tears itself down after `AIFY_HERMES_NO_TUI_TEARDOWN_CYCLES` polls with no
TUI attached (after a `AIFY_HERMES_NO_TUI_GRACE_MS` start grace), so a delivery with no visible TUI
fails rather than requeueing forever.

## Leftover `hermes.exe` processes, or `Session X already has a live owner`

- **Stop and relaunch reap the whole set.** The wrapper's exit trap stops the loop and its gateway
  when the TUI exits, and each gateway carries its launcher's lease pid as `HERMES_PARENT_PID`, so it
  ends with its agent.
- **Kill-prior** (`hermes-prior-reap.mjs`) runs when a new instance of the agent starts. It collects
  a previous generation's gateway on the agent's own port and whatever hermes records as the owner
  of the agent's session, and never kills its own ancestry. A `live owner` refusal clears on a
  relaunch.
- **After a hard kill** of the host tier, leftovers are reported, never killed, by
  `aify-comms doctor`: `gateway-orphans` (gateway hosts, including an elevated one it can only see
  in the socket table, shown `unidentified`) and `managed-orphans` (delivery loops). Removing them is
  the operator's call.

## Hermes starts a FRESH session after a host-tier restart

**Symptom.** An agent resumes while the host stays up, but after a stop and start it comes up in a
new session with its history gone.

hermes has two ids per session: a durable `session_key` (`YYYYMMDD_HHMMSS_hex`, what `--resume`
needs) and an ephemeral gateway `sid` (short hex, regenerated on every gateway restart). The resume
marker `aify-hermes-session-<agent>` in the temp directory must hold the durable key:

- `resolve-session` (`runResolveSessionCli`) checks the marker against hermes' SessionDB. A real key
  resumes; a key the DB positively reports gone starts fresh and clears the marker; an unreachable
  gateway leaves the marker alone.
- `startResumeMarkerSync` in the delivery loop rewrites the marker from the live session every ~20s,
  so sessions the operator starts by typing are captured too.
- hermes deletes sessions that never had a turn, so an agent whose workspace has **no** saved
  sessions starting fresh is correct.

**Check.** `cat ${TMPDIR:-/tmp}/aify-hermes-session-<agent>` should be a `YYYYMMDD_HHMMSS_hex` key
before and after a restart. Whether hermes still has it, with hermes' own Python:
`python3 -c "from hermes_state import SessionDB; print(SessionDB().get_session('<key>'))"`
(`None` = gone, fresh is correct). To restore a known conversation, set its durable key with
Dashboard **Set handle**.
