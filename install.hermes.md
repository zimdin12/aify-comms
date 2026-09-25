# Install For Hermes

Connects Hermes Agent to an aify-comms service: the `hermes-aify` launcher, the MCP server, the aify
Hermes plugin, turn hooks and skills. The service must already be running (see
[README.md](README.md#quick-start), which also lists the prerequisites).

## Prerequisites beyond the README's

- **Hermes Agent**, with `hermes` on PATH. If it lives elsewhere, set `AIFY_HERMES_COMMAND` to the
  executable before installing; the installer and `hermes-aify` both read it.
- **Node.js 22 or newer** where `hermes-aify` runs: the Hermes TUI's gateway client uses the global
  `WebSocket`, which older Node lacks, and fails as `gateway exited`.

## Install

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/aify-comms
cd ~/aify-comms
bash install.sh --client hermes http://<service-host>:8800 --with-hook
```

- With no URL, `install.sh` asks for one when run in a terminal and otherwise uses
  `http://127.0.0.1:8800`.
- It writes the service into `~/.aify/services.json`, which is how aify-env learns the service exists.
- It writes into Hermes' active config home, which on native Windows is often
  `%LOCALAPPDATA%\hermes` rather than `~/.hermes`. `hermes config path` and `hermes mcp list` show
  where. Re-running replaces the `aify-comms` block in `config.yaml` in place.
- It decides at install time whether the wrapped `hermes` is a Linux or a native Windows binary and
  bakes the matching path style. Re-run it if you switch which Hermes `hermes-aify` wraps.

**Re-run `install.sh --client hermes` after every Hermes update.** An update deletes the prebuilt
`hermes_cli/web_dist` bundle, the gateway host runs `hermes dashboard --skip-build` and dies without
it, and the installer rebuilds it (`npm install && npm run build` in Hermes' `web/`). Set
`AIFY_HERMES_INSTALL_ROOT` if the installer cannot find the Hermes install root. Run `hermes update`
from an ordinary terminal: from an Administrator one it can relaunch a gateway elevated, where no aify
cleanup can stop it.

Restart Hermes afterwards: a running session keeps the MCP server it loaded.

## Run agents on this host

Managed agents are started by [aify-env](https://github.com/zimdin12/aify-env), a separate repo. After
the install above, clone it, run its `./install.sh` (it asks for the service key this host is
missing; `npm install -g` installs the command and cannot notice one), then start it in the directory
that holds your workspaces:

```bash
cd /path/to/workspaces
aify-env
```

It spawns only into workspaces under that directory, and offers Hermes when the `hermes-aify`
launcher this install wrote is on the PATH it was started with. Starting a second `aify-env` for the
same environment replaces the first, and the one replaced stops its managed agents, so starting it is
the operator's call. Ask a running one with `aify-env doctor`. More in
[docs/BRIDGE_SETUP.md](docs/BRIDGE_SETUP.md).

## Confirm it took effect

```bash
aify-comms doctor          # --json for scripts, --strict to exit non-zero on a failure
```

`service`, `bridge-installed` and `skills-installed` should be green. Then relaunch every agent that
was running before the install, because a running agent keeps the bridge code it loaded.
`bridge-current` names any registered agent still reporting an older build, and reads `unknown` until
agents report one. `aify-comms` only verifies (`doctor`, `--check`, `--version`, `--help`); anything
else exits 2.

## Start a Hermes agent

```bash
hermes-aify --aify-agent <agent-id>                          # resumes the agent's stored session
hermes-aify --aify-agent <agent-id> --resume <session-id>    # a specific session
hermes-aify --shared --aify-agent <agent-id>                 # aify-env owns the terminal
```

- **Pass `--aify-agent` for a registered agent.** The agent registers under that id at startup, and
  the id reaches the turn hooks only through the launch environment; started without it, its status
  stops tracking its turns.
- `--resume <session-id>` without `--aify-agent` recovers the agent from the handle: `aify-<id>` maps
  to `<id>` directly, and any other handle is looked up on the service, which works only while the
  service has no API key, because the launcher sends none.
- Starting `hermes-aify --aify-agent <id>` in a terminal replaces that agent's live instance on this
  host, a managed worker included, with its gateway host and delivery loop. An automatic start (a
  message waking the agent, `comms_spawn`) is refused with exit 75 instead. The rules are in
  aify-wrapper's README, "One live instance per agent".
- `--shared` hands the terminal to aify-env; `aify-env attach <agent>` reattaches and `Ctrl+]`
  detaches.
- `hermes-aify` passes `--yolo` by default, and the gateway host runs with `HERMES_YOLO_MODE=1`, so
  approval prompts are skipped; `--safe` (or `--no-auto`) keeps them in the visible TUI.
- `AIFY_HERMES_DISABLE_PLUGIN=1` launches without the aify Hermes plugin, for comparing against
  upstream Hermes. The plugin is what binds the visible session for delivery, so leave it on otherwise.

Hermes names MCP tools with a server prefix: in a Hermes turn they are `mcp_aify_comms_comms_send`,
`mcp_aify_comms_comms_agent_info` and so on. Agents answer a message with
`comms_send(type="response", inReplyTo="<message id>", to="<sender>")`; final text, stdout and run
summaries are not the reply. The `aify-comms` skill has the rest.

Register a Hermes agent only from inside `hermes-aify`. A record written by hand with
`POST /api/v1/agents` has no live bridge behind it, reads `offline`, and refuses sends until
`hermes-aify` is restarted.

## How messages reach Hermes

Resident and managed agents work the same way. `hermes-aify` starts three processes:

1. A hidden gateway host, `hermes dashboard --port <P> --host 127.0.0.1 --no-open --skip-build`,
   with `HERMES_DASHBOARD_TUI=1` so it serves the `/api/ws` gateway.
2. A delivery loop, `hermes-managed-host.js run <agent>`, which claims the agent's messages and sends
   each to the live session over that gateway: `prompt.submit` while idle, `session.steer` while a
   turn runs. A rejected or racing busy delivery is requeued rather than interrupting the turn.
3. The visible `hermes --tui`, attached to the same gateway and resumed on the agent's native Hermes
   session id, which is the agent's `sessionHandle`.

The gateway host carries the launcher's lease pid as `HERMES_PARENT_PID`, so Hermes' own watchdog
ends it with the agent, and the next start of the agent stops anything a hard kill left behind. After
a hard kill of aify-env, `aify-comms doctor` `gateway-orphans` names gateways nothing owns.

**When a live Hermes turn has no `mcp_aify_comms_*` tools**, its gateway loaded before the MCP server:
restart `hermes-aify`. **When a send fails with `visible session not found`**, the terminal was started
by an older launcher, with the plugin disabled, or before the TUI attached: re-run the installer,
restart that `hermes-aify`, and register again from inside it. Gateway host logs are in
`~/.local/state/aify-comms/hermes-gateway-host-<port>.log` (`$XDG_STATE_HOME/aify-comms/` when set).

## What the install writes

- The `aify-comms` MCP entry in Hermes' active config file.
- `hermes-aify` and the `aify-comms` verifier in `~/.local/bin`, plus `.cmd` shims on Git Bash as in
  [install.claude.md](install.claude.md#windows). `hermes-aify` exports `AIFY_COMMS_URL` and loads
  `integrations/hermes-aify-plugin`.
- Skills in the Hermes skills tree.
- The bridge runtime, copied to `~/.aify-comms` (`AIFY_HOME` overrides). An edit under `mcp/stdio/`
  reaches agents only after `install.sh` re-runs and the agents relaunch.
- Shell hooks under Hermes' own config root, in `agent-hooks/`: `aify-turn-start.sh` on
  `pre_llm_call`, `aify-turn-end.sh` on `on_session_end`, and `aify-blocked.sh` / `aify-unblocked.sh`
  on `pre_approval_request` / `post_approval_response`, posting through `agent-state-event.mjs`, which
  carries the API key. Hermes runs a shell hook only after its exact command is approved once
  (`hermes hooks list`). The bridge also sets and clears `working` from the gateway's own state,
  which covers a turn that ended without `on_session_end`, such as one ended by an API error.
- With `--with-hook`, a `post_tool_call` hook running `notify-check.js`, which tells the agent about
  unread messages without marking them read. Once installed, re-runs keep it.

## OpenAI usage check

At the end of every install, `install.sh` prints a `[usage]` line saying whether an OpenAI token
works. Hermes delegates OpenAI auth to the codex CLI's store, so the token comes from `codex login`.
Nothing collects subscription quota at the moment (`mcp/stdio/usage-collector.js` has no caller), so
the dashboard's quota figures are not live.

## More

[docs/HERMES_INTEGRATION.md](docs/HERMES_INTEGRATION.md) covers the integration in depth. Herdr pane
restore and the aify-wrapper commands work as in [install.claude.md](install.claude.md#herdr). The
`aify-comms-debug` skill, installed above, covers delivery, status and console problems.
