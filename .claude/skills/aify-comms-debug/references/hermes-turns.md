# aify-comms debug: Hermes turns, tools, status and native fallback

## Every managed-hermes dispatch fails "Queued >180s … up-but-deaf"

**Symptom.** Every hermes agent bounces at once while claude agents on the same host work; the
Console flashes and closes; the worker's console tail or the wrapper says
`FATAL: managed gateway host for '<id>' did not come up`.

**Cause.** The hidden gateway host (`hermes dashboard --port <P> --host 127.0.0.1 --no-open
--skip-build`, started by `ensureGatewayHost` in `hermes-gateway.mjs`) did not come up, so no
delivery loop ever claims. Almost always hermes itself: a broken or half-finished `hermes update`
(`npm error Missing script: "build"` during "Installing TUI dependencies"), or a hermes with no
built web UI.

**Triage.** Read `~/.local/state/aify-comms/hermes-gateway-host-<port>.log`; the host fails fast
with a named cause for the npm-build case and waits 60s otherwise. Readiness also opens the
`/api/ws` socket, because hermes' `web_server.py` can serve its index while refusing the socket
(`AIFY_HERMES_VERIFY_WS=0` turns that probe off). Reproduce by hand:
`HERMES_DASHBOARD_TUI=1 hermes dashboard --port 9199 --host 127.0.0.1 --no-open --skip-build`
should come up within seconds.

**Recovery.** Fix hermes (`hermes update`), confirm the command above, then restart each worker
from the dashboard (Sessions → Restart). `install.sh` is needed only when hermes' CLI interface
changed.

## `hermes mcp test` works, but the live turn has no aify tools

`hermes mcp test` is a separate process; the visible TUI runs against the gateway host, which needs
the aify plugin (`integrations/hermes-aify-plugin`) to run `discover_mcp_tools()` before the agent
is built. Run `bash install.sh --client hermes` and relaunch `hermes-aify`. Register through the
prefixed MCP tools, not direct HTTP.

If `mcp_aify_comms_comms_register` succeeds but `comms_agent_info` shows
`wakeMode: hermes-missing-handle`, the MCP child registered without a gateway URL: see
hermes-session.md.

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
calls. It runs in the managed delivery loop and in a resident bridge with `AIFY_HERMES_GATEWAY_URL`
set. A resident without a gateway has no detector and reads `offline` anyway.

A managed hermes whose screen shows nothing of its turn is why an idle aify-env observation never
ends a held turn (status-model.md). If it still never shows `working`, relaunch `hermes-aify` so
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
