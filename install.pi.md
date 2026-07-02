# Install For Oh My Pi

Use aify-comms when you want dashboard-driven coordination for Pi agents: live direct messages, channels, shared artifacts, active dispatch, managed agent spawn, and environment control.

## Copy-Paste Install

Install Oh My Pi first so the `omp` command is available in the same shell/user that will run the bridge.

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/aify-comms
cd ~/aify-comms
# Pi/OMP wrapper install is intentionally disabled. Managed Pi uses the
# environment bridge plus persistent `omp --mode rpc`, not `omp-aify`.
```

There is no resident Pi wrapper install step for normal use. Restart any long-running `aify-comms` bridge after updating the repo so managed Pi loads the current controller code.

For dashboard-managed spawns, also connect an environment bridge on the machine that should run Pi:

```bash
cd /path/to/workspace-or-workspace-parent
aify-comms
```

On Linux, macOS, or WSL use `aify-comms`. On native Windows from PowerShell/cmd use `aify-comms.cmd`. The service URL defaults to `http://localhost:8800`; the current directory is always an allowed workspace root; extra root arguments are optional safety boundaries, not the per-agent project choice. The OMP resident wrapper install is disabled by default; managed Pi delivery does not need an MCP config inside OMP because the environment bridge drives plain `omp --mode rpc` directly.

## Pi Wrapper Sessions

`omp-aify` / `pi-aify` are intentionally not installed by the normal installer. They were presence-only wrappers and did not provide the main console-wake contract because OMP's RPC channel is single-client.

Historical/manual wrapper usage looked like this, but should not be used for triggerable delivery:

```bash
cd /path/to/project
omp-aify --aify-agent my-pi --aify-role coder
```

If you are resuming a known Pi session, pass the resume handle so aify-comms can record the same native session for presence/debug metadata:

```bash
omp-aify --aify-agent my-pi --resume <session-id-or-prefix>
```

OMP's RPC channel is single-client, so aify-comms does not inject dashboard messages into an already-open Pi TUI. Triggerable Pi delivery is managed RPC (`omp --mode rpc`) through the environment bridge.

## Managed Pi

Managed Pi agents are spawned from the dashboard or `comms_spawn(..., runtime="pi")`. The bridge uses OMP RPC mode (`omp --mode rpc`) so it can send prompts, capture streamed assistant text, steer active work, and interrupt through the runtime boundary. The RPC child is **persistent**: spawned on the first managed dispatch and reused across subsequent ones — see *Delivery path* below.

Current Pi note:
- Runtime aliases `pi`, `omp`, `oh-my-pi`, and `pi-agent` normalize to `pi`.
- Managed Pi supports persistent managed work, resume handles when OMP exposes them, active-run steer, and interrupt. It is the triggerable delivery path for Pi.
- The dashboard's Console pane shows a synthesized terminal stream for the persistent RPC child — see *Delivery path*.
- Managed Pi captures streamed `text_delta` output and final assistant text from RPC completion events such as `message_end` / `agent_end`.
- A blank model, or a stored model value of `default`, means no `--model` override; Oh My Pi then uses `~/.omp/agent/config.yml`.
- Dashboard **Settings -> Runtime** can set managed Pi model and effort defaults. Pi effort is passed to OMP as `--thinking` when set.
- When managed OMP later reports a native `sessionId`, aify stores it as the agent/session handle so the dashboard edit field and future resumes can use it.
- Steering sends OMP's native RPC `steer` command into the active Pi run. Use `queueIfBusy=true` when a message should wait for the next turn instead.
- Managed runtime hard timeout is **12 hours** by default (`runtimeConfig.timeoutMs`).
- Set `AIFY_PI_COMMAND` or `PI_COMMAND` before starting `aify-comms` if `omp` is installed somewhere that is not on the bridge process `PATH`.

## Delivery path

Managed-pi dispatches drive a **persistent** `omp --mode rpc` child per agent. The first managed dispatch spawns the child; every later dispatch on the same agent reuses it. Each `AgentSessionEvent` the child emits (`message_update`, `tool_execution_start`/`_end`, `RpcExtensionUIRequest`, `agent_start`/`_end`, `error`) is formatted by the bridge into a human-readable terminal frame and pushed into a **synthesized** `terminal_session` row (`status='running'`, `command='aify://virtual-rpc/pi'`). The dashboard's Console pane shows that synthesized stream, so the operator sees what the bridge sees even though there is no PTY. The bridge does NOT depend on aify-comms loading as an MCP server inside the omp session for delivery, so `omp-aify`/`pi-aify` does NOT need the `--strict-mcp-config` isolation that `claude-aify` requires.

Operator console input typed into the synthesized terminal is buffered until `\r`/`\n`, echoed back into the stream, and dispatched as a new RPC turn through the persistent child — same plumbing as a dashboard chat dispatch, no separate wakeup. The child idle-times out after 24 hours of no use (`AIFY_PI_IDLE_TIMEOUT_MS` overrides); the next dispatch respawns on demand.

The agent's `runtime_state.virtualTerminal=true` flag lets the dashboard render this as a bridge-driven feed rather than a real PTY. A real PTY for managed pi is no longer created on dispatch, so the legacy "stop the stale wrapper PTY" advice no longer applies.

### Single-process mutex

Two omp processes on the same OMP session-id corrupt each other's session file — OMP's RPC channel has no multiplexing, see upstream [#436](https://github.com/can1357/oh-my-pi/issues/436). To prevent this, `omp-aify`/`pi-aify` queries `GET /agents/{id}/pi-session-state` before exec'ing omp. When the bridge currently drives the agent's session (a non-stopped virtual terminal row exists), the wrapper refuses with:

> Agent '<id>' is currently driven by aify-comms (visible in dashboard terminal). Stop it from the dashboard or use `omp-aify --standalone --aify-agent <id>` to launch a parallel session on a different session-id.

Choices when this fires:
- **Stop the bridge session from the dashboard** (Console pane → Stop). Then re-run `omp-aify`.
- **Launch a parallel session.** Pass `--standalone` and use a different `--resume <other-handle>`. The bridge will keep driving its own session-id; you'll have a second OMP process on a separate handle. They will not contend.

The check is fail-open: missing `AIFY_COMMS_URL`, a 2-second curl timeout, or a non-pi runtime all cause the wrapper to proceed normally.

### Session rediscover (added 2026-05-26, Plan 6 B3)

The Phase-4 watchdog above already captures the `pi-session-state` response body. Plan 6 B3 reuses that capture: it parses `"sessionId":"<id>"` from the body and overwrites `PI_SESSION_ID` / `AIFY_SESSION_HANDLE` so the inner aify-comms MCP bridge registers with the runtime's authoritative session id, not whatever stale value the operator's shell inherited from a prior pi run. No second HTTP call — the watchdog and rediscover share one curl. Failures are non-fatal: an empty body (pi not running yet on the resident-start path) or a response without `sessionId` leaves the env value alone, and the bridge's discover-first heartbeat (Plan 6 A1) corrects any drift within 60s.

## Session-mode flag

`omp-aify` / `pi-aify` accepts `--resident` and `--managed` to declare its session mode explicitly. Order of precedence:

1. `AIFY_SESSION_MODE` env (`resident` or `managed`) — bridge-spawned PTYs always set this to `managed`.
2. `--resident` / `--managed` flag on the wrapper command line.
3. TTY auto-detect via `[ -t 0 ]` — interactive operator launches default `resident`; non-TTY launches default `managed`.

Most operators don't need the flag — running `omp-aify` from a terminal Just Works as a presence/standalone operator session, and managed dashboard delivery uses the bridge's persistent RPC child. Use `--resident` or `--managed` only when debugging wrapper metadata; it does not turn an open OMP TUI into a multi-client resident injection target.

## Quick Start

```text
comms_register(agentId="my-pi", role="coder", runtime="pi", sessionHandle="$PI_SESSION_ID")
comms_agents()
comms_agent_info(agentId="my-pi")
comms_send(from="my-pi", to="other-agent", type="info", subject="Hello", body="Hi there")
comms_inbox(agentId="my-pi", mode="headers")
comms_inbox(agentId="my-pi", messageId="<message id>")
```
