# Install For Codex

Connects Codex to an aify-comms service: the `codex-aify` launcher, the MCP server, turn hooks and
skills. The service must already be running (see [README.md](README.md#quick-start), which also lists
the prerequisites).

## Install

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/aify-comms
cd ~/aify-comms
bash install.sh --client codex http://<service-host>:8800 --with-hook
```

- With no URL, `install.sh` asks for one when run in a terminal and otherwise uses
  `http://127.0.0.1:8800`.
- It writes the service into `~/.aify/services.json`, which is how aify-env learns the service exists.
- Running it again is how you update. `install.sh --help` lists the flags.
- If Codex lives in WSL, install from WSL: a Windows Codex and a WSL Codex keep separate thread stores.

Restart Codex afterwards: a running session keeps the MCP server it loaded.

## Run agents on this host

Managed agents are started by [aify-env](https://github.com/zimdin12/aify-env), a separate repo. After
the install above, clone it, run its `./install.sh` (it asks for the service key this host is
missing; `npm install -g` installs the command and cannot notice one), then start it in the directory
that holds your workspaces:

```bash
cd /path/to/workspaces
aify-env
```

It spawns only into workspaces under that directory, and offers Codex when the `codex-aify` launcher
this install wrote is on the PATH it was started with. Starting a second `aify-env` for the same
environment replaces the first, and the one replaced stops its managed agents, so starting it is the
operator's call. Ask a running one with `aify-env doctor`. More in
[docs/BRIDGE_SETUP.md](docs/BRIDGE_SETUP.md).

## Confirm it took effect

```bash
aify-comms doctor          # --json for scripts, --strict to exit non-zero on a failure
```

`service`, `bridge-installed` and `skills-installed` should be green. Then relaunch every agent that
was running before the install, because a running agent keeps the bridge code it loaded.
`bridge-current` names any live bridge still running an older build, and reads `unknown-all` until
bridges started on 0.7.0 or later report one. `aify-comms` only verifies (`doctor`, `--check`, `--version`, `--help`); anything
else exits 2.

## Start a Codex agent

```bash
codex-aify --aify-agent <agent-id>                        # a resident agent in this terminal
codex-aify --aify-agent <agent-id> --resume <thread-id>   # continue its thread
codex-aify --shared --aify-agent <agent-id>               # aify-env owns the terminal
```

`codex-aify` starts a local `codex app-server --listen ws://127.0.0.1:<port>`, runs the TUI against
it with `codex --remote`, and exports the address as `AIFY_CODEX_APP_SERVER_URL`. The agent's bridge
delivers messages by starting turns on that app-server, in the thread the TUI shows. Codex's
`--remote` TUI may not draw a turn started by another client until later
([openai/codex#15320](https://github.com/openai/codex/issues/15320)).

- **Pass `--aify-agent` for a registered agent.** The agent registers under that id at startup, and
  the id reaches the turn hooks and the rollout detector only through the launch environment. Started
  without it, the agent still messages, but its status stops tracking its turns; relaunch to fix it.
- `--resume <thread-id>` without `--aify-agent` asks the service which Codex agent owns that thread,
  using this host's API key. If the service is unreachable or names no agent, it prints `NO AGENT ID`
  and carries on anonymous.
- A fresh launch binds no thread until Codex reports one. `--resume <id>` exports `CODEX_THREAD_ID`
  and `AIFY_SESSION_HANDLE`; if `~/.codex/sessions` has no such thread, Codex starts fresh.
- Starting `codex-aify --aify-agent <id>` in a terminal replaces that agent's live instance on this
  host, a managed worker and its app-server included. An automatic start (a message waking the agent,
  `comms_spawn`) is refused with exit 75 instead. The rules are in aify-wrapper's README, "One live
  instance per agent".
- `--shared` hands the terminal to aify-env; `aify-env attach <agent>` reattaches and `Ctrl+]`
  detaches.
- `codex-aify` passes `--dangerously-bypass-approvals-and-sandbox` by default; `--safe` (or
  `--no-auto`) keeps Codex's approval prompts.

`comms_agent_info(agentId="<agent-id>")` from inside the session shows its status and session handle.
Agents answer a message with
`comms_send(type="response", inReplyTo="<message id>", to="<sender>")`; final text, stdout and run
summaries are not the reply. The `aify-comms` skill has the rest.

**Windows paths.** Register and spawn with forward slashes (`C:/Users/you/project`). Codex rejects a
backslash path with `Invalid request: AbsolutePathBuf deserialized without a base path`, which fails
every run for that agent.

## What the install writes

- The `aify-comms` MCP server, through `codex mcp add`.
- Skills in `$CODEX_HOME/skills` (default `~/.codex`).
- `codex-aify` and the `aify-comms` verifier in `~/.local/bin`; on Git Bash also `.cmd` shims, as in
  [install.claude.md](install.claude.md#windows).
- The bridge runtime, copied to `~/.aify-comms` (`AIFY_HOME` overrides). An edit under `mcp/stdio/`
  reaches agents only after `install.sh` re-runs and the agents relaunch.
- Turn hooks in `$CODEX_HOME/hooks.json`: `UserPromptSubmit` (start), `Stop` and `Interrupt` (end),
  `PermissionRequest` (blocked) and `PostToolUse` (unblocked), posting through
  `agent-state-event.mjs`, which carries the API key. Codex runs hooks only with `hooks = true` under
  `[features]` in `config.toml`; the installer writes it, and renames an older `codex_hooks` key.
- With `--with-hook`, a `PostToolUse` hook running `notify-check.js`, which tells the agent about
  unread messages without marking them read. Once installed, re-runs keep it.

## Managed Codex defaults

Model blank (Codex's own default) and effort `high`. Change them in the dashboard under
**Settings → Managed workers**. Saving changes new workers only; **Apply model and effort to existing
workers** gives the saved values to the existing ones, each at its next start.

## OpenAI usage check

At the end of every install, `install.sh` prints a `[usage]` line saying whether the OpenAI token
from `codex login` works; `node ~/.aify-comms/mcp/stdio/usage-preflight.js --json` gives the same
answer as `{ok, code}`. The service reads the OpenAI quota pool itself from that token (`GET /usage`,
cached 120 s). Nothing collects the Anthropic pool (`mcp/stdio/usage-collector.js` has no caller), so
it reads `?` or `stale`.

## Herdr and troubleshooting

Herdr pane restore and the aify-wrapper commands work as described in
[install.claude.md](install.claude.md#herdr). The `aify-comms-debug` skill, installed above, covers
delivery, status and console problems, including `AbsolutePathBuf`.
