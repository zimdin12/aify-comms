# aify-comms debug: Hermes: gateway, sessions, turns, tools and processes

## How a hermes agent is put together

One `hermes-aify` launch runs three processes per agent:

- **gateway host**: a hidden `hermes dashboard --port <P>` started by `ensureGatewayHost`
  (`hermes-gateway.mjs`). Its stderr goes to
  `~/.local/state/aify-comms/hermes-gateway-host-<port>.log`, truncated on each spawn.
- **delivery loop**: `hermes-managed-host.js run <agent>`, the agent's `channel-sidecar` claimer. It
  submits with `prompt.submit` when idle and `session.steer` when busy.
- **visible TUI**: `hermes --tui`, attached to that gateway through `HERMES_TUI_GATEWAY_URL`.

Each agent gets its own port, recorded in an `aify-hermes-port-<agent>` marker in the temp directory.
For any delivery question read `comms_run_status(runId=...)` first, then the gateway-host log.

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
`FATAL: managed gateway host for '<id>' did not come up`: see "Every managed-hermes dispatch fails" below.

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
  [operator].

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

**Check [host].** `cat "${TEMP:-${TMP:-/tmp}}/aify-hermes-session-<agent>"` should be a `YYYYMMDD_HHMMSS_hex` key
before and after a restart. Whether hermes still has it, with hermes' own Python:
`python3 -c "from hermes_state import SessionDB; print(SessionDB().get_session('<key>'))"`
(`None` = gone, fresh is correct). To restore a known conversation, set its durable key in the
agent's **Edit…** → *Native session handle* field.

## Every managed-hermes dispatch fails "Queued >180s … up-but-deaf"

**Symptom.** Every hermes agent bounces at once while claude agents on the same host work; the
Console flashes and closes; the worker's console tail or the wrapper says
`FATAL: managed gateway host for '<id>' did not come up`.

**Cause.** The hidden gateway host (`hermes dashboard --port <P> --host 127.0.0.1 --no-open
--skip-build`, started by `ensureGatewayHost` in `hermes-gateway.mjs`) did not come up, so no
delivery loop ever claims. Almost always hermes itself: a broken or half-finished `hermes update`
(`npm error Missing script: "build"` during "Installing TUI dependencies"), or a hermes with no
built web UI.

**Triage [host].** Read `~/.local/state/aify-comms/hermes-gateway-host-<port>.log`; the host fails fast
with a named cause for the npm-build case and waits 60s otherwise. Readiness also opens the
`/api/ws` socket, because hermes' `web_server.py` can serve its index while refusing the socket
(`AIFY_HERMES_VERIFY_WS=0` turns that probe off). Reproduce by hand:
`HERMES_DASHBOARD_TUI=1 hermes dashboard --port 9199 --host 127.0.0.1 --no-open --skip-build`
should come up within seconds.

**Recovery [host].** `hermes update` from a non-admin terminal, then `bash install.sh --client hermes`
(an update deletes the web bundle `--skip-build` needs). Confirm the command above, then restart
each worker with `comms_restart(agentId)`.

## `hermes mcp test` works, but the live turn has no aify tools

`hermes mcp test` is a separate process; the visible TUI runs against the gateway host, which needs
the aify plugin (`integrations/hermes-aify-plugin`) to run `discover_mcp_tools()` before the agent
is built. Run `bash install.sh --client hermes` and relaunch `hermes-aify`. Register through the
prefixed MCP tools, not direct HTTP.

If `mcp_aify_comms_comms_register` succeeds but `comms_agent_info` shows
`wakeMode: hermes-missing-handle`, the MCP child registered without a gateway URL: see
"Resident hermes reads `offline`" above.

## Hermes fails at once with `'NoneType' object is not iterable`

A Responses-API streaming edge case (the stream ends with a `null` output list), not an aify
registration error. The aify plugin handles it by rebuilding the output from the streamed items.
Check that the wrapper loads the plugin (`grep AIFY_HERMES_PLUGIN ~/.local/bin/hermes-aify`), rerun
`bash install.sh --client hermes`, and relaunch. On native Windows `hermes-aify.cmd` must call
`hermes-aify.ps1`; a `hermes-tui: no TTY` exit means it does not. The TUI's active-session file is
`${TMPDIR:-/tmp}/aify-hermes-active-<agent>.json`. `AIFY_HERMES_DISABLE_PLUGIN=1 hermes-aify`
runs upstream hermes without the plugin for comparison.

## Hermes never shows `working`, or reads `online` mid-turn

`hermes-gateway-turn-detector.js` reads the gateway session status about every 3s: `working` sets
the turn, and idle clears it only after several consecutive idle reads
(`AIFY_HERMES_GATEWAY_TURN_IDLE_DEBOUNCE`), because hermes' running flag blinks off between tool
calls. A resident without a gateway has no detector and reads `offline` anyway.

A managed hermes whose screen shows nothing of its turn is why an idle aify-env observation never
ends a held turn (status-model.md, "How `derive()` decides"). If it still never shows `working`, relaunch `hermes-aify` so
the loop loads current code.

## Native fallback: ACP persistent session

Used only when wrapper-backed delivery is off, or the Console command is
`aify://virtual-rpc/hermes`: a long-lived `hermes acp --accept-hooks` child per agent
(`mcp/stdio/hermes-session.js`).

- **Stuck at `[hermes] thinking...`:** Stop the worker from the dashboard Console and re-send; the
  bridge starts a fresh `hermes acp`.
- **`hermes acp handshake timeout`:** `hermes acp --check` must exit 0 on that host. A custom
  `AIFY_HERMES_ACP_COMMAND` must keep `--accept-hooks`. The timeout error carries the tail of
  hermes' stderr (missing provider credentials, `~/.hermes/.env` not loaded).
- **terminal/\* callbacks declined:** by design; the bridge hosts no tool subprocesses. Configure a
  sandbox provider in hermes.
