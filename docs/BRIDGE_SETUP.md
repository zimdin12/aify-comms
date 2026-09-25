# Host Tier Setup

The service stores everything and launches nothing. On each machine that should run agents,
[aify-env](https://github.com/zimdin12/aify-env) does: it heartbeats an environment to the service,
claims spawn requests, runs the launchers in real terminals, streams their consoles to the dashboard
and carries keystrokes back. The `aify-comms` command on a host only verifies (`doctor`, `--check`,
`--version`, `--help`).

## Quick model

- One service, usually with Docker Compose.
- One aify-env per execution environment: native Windows, each WSL distro, each Linux or macOS host.
  The dashboard's **Environments** page shows each one, `online` while it heartbeats.
- aify-env spawns only into workspaces under the directory it was started in. It takes no root
  arguments: `aify-env <path>` exits 64 as an unknown subcommand.
- It serves the services listed in `~/.aify/services.json`, which it reads once, when it starts.
  `install.sh --client <runtime>` writes the aify-comms entry, so install a client first.
- It offers a runtime when that runtime's launcher (`claude-aify`, `codex-aify`, `hermes-aify`) is on
  the PATH it was started with, and its workers inherit its environment.
- **Starting one is the operator's action.** A second aify-env for the same environment replaces the
  first, and the one replaced stops its managed agents. Ask a running one with `aify-env doctor`.

## Set up a host

```bash
cd /path/to/aify-comms
bash install.sh --client codex http://<service-host>:8800 --with-hook   # registers the service

git clone https://github.com/zimdin12/aify-env
cd aify-env && ./install.sh          # asks for the service key this host is missing

cd /path/to/workspaces
aify-env
```

Then `aify-env doctor` (its `terminal` row says whether this host can open a terminal), and the
**Environments** page. Leave aify-env running while you use the dashboard; stopping it stops the
managed agents it runs.

Per environment:

- **Native Windows:** install from Git Bash, so the launchers get `.cmd` shims in
  `%USERPROFILE%\.local\bin`. Start aify-env in a directory that covers your `C:/...` workspaces, and
  use forward-slash paths in the dashboard.
- **WSL:** install and start inside the distro that owns the runtime CLI. Workspaces are Linux paths
  such as `/mnt/c/Docker/project`.
- **Linux, macOS or a remote host:** the same, with a service URL that host can reach.

## Service URL

A host uses the URL given to `install.sh --client`, which is baked into the launchers and written to
`~/.aify/services.json`.

- Same machine as the service: `http://localhost:8800`. The bridge rewrites `localhost` to
  `127.0.0.1`, because Docker Desktop forwards IPv6 `::1` unreliably.
- From inside a container to a service on the host: usually `http://host.docker.internal:8800`.
- Another machine: the LAN or VPN address, for example `http://10.0.0.20:8800`.

If a host does not appear on the Environments page, check it can reach the service:
`curl http://<service-host>:8800/health`.

## When a host goes away

Stopping aify-env stops the execution target, not the agents. An environment with no heartbeat for
about 90 seconds reads offline, and so do the managed agents it hosted. Chats, identities, spawn specs
and session records remain. Start aify-env again (the operator's action), or assign the agent to
another online environment in the dashboard, then restart it.

Forgetting an environment hides it from the dashboard lists and deletes no agents, chats or records.
It reappears if it heartbeats again.

## Managed and resident agents

- **Managed:** spawned from **Environments → Spawn Session** or `comms_spawn`, owned by the aify-env
  that started it, and restarted, stopped, compacted or reset from the dashboard.
- **Resident:** a terminal you open yourself with `claude-aify`, `codex-aify` or `hermes-aify`
  `--aify-agent <id>`. Add `--shared` and aify-env owns the terminal, so closing the window does not
  end it.

Switch an agent with **Switch to managed** / **Switch to resident**, in its details drawer or on its
row in **Sessions**. Ownership changes only when you switch: a resident that registers while the
agent is managed is recorded as a candidate and does not take over, and a send to a resident that has
gone away fails visibly instead of being sent elsewhere.

To continue a managed agent's conversation in your own terminal, the dashboard's **Continue in CLI**
gives the exact command, which has this shape:

```bash
claude-aify --aify-agent <id> --resume <session-id>
codex-aify  --aify-agent <id> --resume <thread-id>
hermes-aify --aify-agent <id> --resume <session-id>
```

Statuses, delivery per runtime, compaction and the runtime settings are in
[OPERATING_MODES.md](OPERATING_MODES.md). Removing everything is in [UNINSTALL.md](UNINSTALL.md).

## Tuning the agent-side bridge

Every agent runs the aify-comms MCP server (`~/.aify-comms/mcp/stdio/server.js`). These variables,
set in the environment that starts the launcher, tune it; the defaults are right for most hosts.

| variable | default | effect |
|---|---|---|
| `AIFY_HTTP_TIMEOUT_MS` | `20000` | timeout for each call to the service |
| `AIFY_DISPATCH_POLL_MS` | `3000` | how often a resident agent polls for work |
| `AIFY_COMMS_CHANNEL_POLL_MS` | `3000` | how often the Claude channel server and the Hermes delivery loop poll |
| `AIFY_SESSION_HEARTBEAT_MS` | `60000` | session heartbeat interval |
| `AIFY_FORCE_REGISTER` | unset | `1` takes over an agent id that another live bridge of the same mode still holds |

Runtime-specific variables are in each guide: `AIFY_CLAUDE_STRICT_MCP`
([install.claude.md](../install.claude.md)), `AIFY_HERMES_COMMAND` and `AIFY_HERMES_DISABLE_PLUGIN`
([install.hermes.md](../install.hermes.md)), `AIFY_PI_COMMAND` ([install.pi.md](../install.pi.md)).
