# Install For Claude Code

Connects Claude Code to an aify-comms service: the `claude-aify` launcher, the two MCP servers, turn
hooks and skills. The service must already be running (see [README.md](README.md#quick-start), which
also lists the prerequisites).

## Install

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/aify-comms
cd ~/aify-comms
bash install.sh --client claude http://<service-host>:8800 --with-hook
```

- With no URL, `install.sh` asks for one when run in a terminal and otherwise uses
  `http://127.0.0.1:8800`.
- It writes the service into `~/.aify/services.json`, which is how aify-env learns the service exists.
- Running it again is how you update. `install.sh --help` lists the flags.

Restart Claude Code afterwards: a running session keeps the MCP servers it loaded.

## Run agents on this host

Managed agents are started by [aify-env](https://github.com/zimdin12/aify-env), a separate repo. After
the install above, clone it, run its `./install.sh` (it asks for the service key this host is
missing; `npm install -g` installs the command and cannot notice one), then start it in the directory
that holds your workspaces:

```bash
cd /path/to/workspaces
aify-env
```

It spawns only into workspaces under that directory, and offers Claude when the `claude-aify` launcher
this install wrote is on the PATH it was started with. Starting a second `aify-env` for the same environment replaces the first,
and the one replaced stops its managed agents, so starting it is the operator's call. Ask a running
one with `aify-env doctor`. More in [docs/BRIDGE_SETUP.md](docs/BRIDGE_SETUP.md).

## Confirm it took effect

```bash
aify-comms doctor          # --json for scripts, --strict to exit non-zero on a failure
```

`service`, `bridge-installed` and `skills-installed` should be green. Then relaunch every agent that
was running before the install, because a running agent keeps the bridge code it loaded.
`bridge-current` names any live bridge still running an older build, and reads `unknown-all` until
bridges started on 0.7.0 or later report one. A row that gathered no evidence reports that as a failure, not a pass.

`aify-comms` only verifies (`doctor`, `--check`, `--version`, `--help`); anything else exits 2.

## Start a Claude agent

```bash
claude-aify --aify-agent <agent-id>                         # a resident agent in this terminal
claude-aify --aify-agent <agent-id> --resume <session-id>   # continue its conversation
claude-aify --shared --aify-agent <agent-id>                # aify-env owns the terminal
```

- **Pass `--aify-agent` for a registered agent.** The agent registers under that id at startup, and
  the id reaches the turn hooks and the transcript detector only through the launch environment. Started
  without it, the agent still messages, but its status stops tracking its turns, and nothing inside
  the running session can fix that: relaunch. The launcher prints `NO AGENT ID` when it has none.
- `--resume <session-id>` without `--aify-agent` looks the agent up by that handle: first on the
  service, using this host's API key, then, if the service is unreachable or names no agent, from
  `${TMPDIR:-/tmp}/aify-claude-session-<agent>.json`.
- Starting `claude-aify --aify-agent <id>` in a terminal replaces that agent's live instance on this
  host, a managed worker included. An automatic start (a message waking the agent, `comms_spawn`) is
  refused with exit 75 instead. The rules are in aify-wrapper's README, "One live instance per agent".
- `--shared` hands the terminal to aify-env, so closing the window does not end the session. It needs
  `aify-env` on PATH; `aify-env attach <agent>` reattaches and `Ctrl+]` detaches.
- `claude-aify` passes `--dangerously-skip-permissions` by default; `--safe` (or `--no-auto`) keeps
  Claude's permission prompts.
- `--resident` / `--managed` set the session mode; without them `AIFY_SESSION_MODE` decides, then
  whether stdin is a terminal.

`comms_agent_info(agentId="<agent-id>")` from inside the session shows its status and session handle.
Agents answer a message with `comms_send(type="response", inReplyTo="<message id>", to="<sender>")`;
final text, stdout and run summaries are not the reply. The `aify-comms` skill has the rest.

## How messages reach Claude

`claude-aify` starts Claude with `--dangerously-load-development-channels server:aify-comms-channel`.
The `aify-comms-channel` MCP server (`claude-channel.js`) claims the agent's messages and delivers
each one into the live session as a `<channel source="aify-comms-channel" ...>` event. If Claude says
`no MCP server configured with that name`, re-run the installer and restart Claude Code.

By default the launcher loads your whole `~/.claude.json` MCP list, where the installer adds the two
aify servers. A Claude Code bug ([#38462](https://github.com/anthropics/claude-code/issues/38462),
[#21341](https://github.com/anthropics/claude-code/issues/21341)) can fail to start MCP servers when
many start at once, leaving the channel server unregistered and messages undelivered. Set
`AIFY_CLAUDE_STRICT_MCP=1` in the launching shell and the launcher loads only the two aify servers.

## What the install writes

- MCP servers `aify-comms` and `aify-comms-channel`, in Claude's user scope.
- Skills in `~/.claude/skills` and slash commands in `~/.claude/commands/aify-comms`.
- `claude-aify` and the `aify-comms` verifier in `~/.local/bin`.
- The bridge runtime, copied to `~/.aify-comms` (`AIFY_HOME` overrides). Launchers and MCP config
  point at that copy, so an edit under `mcp/stdio/` reaches agents only after `install.sh` re-runs and
  the agents relaunch.
- Turn hooks in `~/.claude/settings.json`, always: `UserPromptSubmit` and `PostToolUse` (turn start),
  `Stop` through `claude-stop-gate.js`, `SessionStart` on compact and `StopFailure` (turn end), and
  `PermissionRequest` (blocked). They post through `agent-state-event.mjs`, which carries the API key,
  and do nothing in a session without `AIFY_AGENT_ID`. The bridge also reads the transcript to set and
  clear `working` when a hook does not fire.
- With `--with-hook`, a `PostToolUse` hook running `notify-check.js`, which tells the agent about
  unread messages without marking them read. Once installed, re-runs keep it.

A malformed `~/.claude/settings.json` is backed up to `<path>.aify-bak-<timestamp>` before the
installer rewrites it.

## Windows

- Install from Git Bash. The installer writes `.cmd` shims (`claude-aify.cmd`, `aify-comms.cmd`) to
  `%USERPROFILE%\.local\bin` and adds that directory to your user PATH; open a new PowerShell
  afterwards. In a window that was already open: `$env:Path += ";$env:USERPROFILE\.local\bin"`, then
  `aify-comms.cmd doctor`.
- Register with forward-slash paths (`C:/path/to/project`).
- The bridge rewrites `http://localhost` to `http://127.0.0.1`: `localhost` can resolve to IPv6
  `::1`, which Docker Desktop forwards unreliably.
- Installing from WSL gives a WSL-only launcher, not a Windows one.

## Managed Claude defaults

Model blank (Claude's own default) and effort `high`. Change them in the dashboard under
**Settings → Managed workers**. Saving changes new workers only; **Apply model and effort to existing
workers** gives the saved values to the existing ones, each at its next start.

## Herdr

The launchers claim their Herdr pane, so an agent started in a Herdr pane comes back as `claude-aify`
after a reboot. It only acts when `HERDR_ENV` is set, can never fail a launch, and logs to
`~/.aify/herdr/claim.log`. Nothing restores until the plugin is linked once with
`aify-herdr-pane install`. That command, `herdr-aify` and `aify-wrapper-check` come from
[aify-wrapper](https://github.com/zimdin12/aify-wrapper), which this install uses internally but does
not put on PATH: run `npm link` once in an aify-wrapper checkout. Design and limits: its `HERDR.md`.

## Troubleshooting

The `aify-comms-debug` skill, installed above, covers delivery, status and console problems.
