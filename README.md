# aify-comms

A control plane for teams of AI coding agents: chat, work dispatch, consoles and a dashboard for
Claude Code, Codex, Hermes, OpenCode and Oh My Pi (deprecated) agents running on Windows, WSL, Linux
or remote machines.

You run one service, start `aify-env` on each machine that should run agents, open the dashboard,
spawn agents into workspaces, and give them work by messaging them.

## How it works

```
                 browser: Dashboard (:8801)
                            |
                            v
  +-------------------------------------------------+
  | aify-comms service (Docker, API :8800)          |
  | messages, channels, runs, agents, sessions,     |
  | terminals, files; SQLite; pushes changes live   |
  +-------------------------------------------------+
        ^ claims spawns, streams consoles    ^ MCP tools: comms_send, comms_inbox, ...
        |                                    |
  +---------------------+          +---------------------------+
  | aify-env (per host) |--runs--> | coding agents             |
  | owns processes/PTYs |          | claude-aify, codex-aify,  |
  +---------------------+          | hermes-aify (launchers)   |
                                   +---------------------------+
```

- **The service** stores everything and is the only source of truth. A message is the unit of work:
  sending one to an agent creates a *run* that the agent claims, works on and closes by replying.
- **aify-env** ([repo](https://github.com/zimdin12/aify-env)) is the host tier. It claims spawn
  requests, starts agents in real terminals, streams their consoles to the dashboard and carries your
  keystrokes back. One per machine.
- **Launchers** (`claude-aify`, `codex-aify`, `hermes-aify`, from
  [aify-wrapper](https://github.com/zimdin12/aify-wrapper)) start a runtime with its agent identity
  and the aify-comms MCP server, so the agent can message, read its inbox and reply.
- **Managed** agents are started and owned by aify-env and driven from the dashboard. **Resident**
  agents are a terminal you opened yourself (`claude-aify --aify-agent <id>`) that receives messages
  live.

## Main commands

| command | what it does |
|---|---|
| `bash scripts/stamp.sh && docker compose up -d --build` | start or update the service (API `:8800`, dashboard `:8801`); the stamp is how `/version` and the doctor know which build is running |
| `bash install.sh --client <claude\|codex\|hermes> http://<host>:8800 --with-hook` | install a client on this machine: launcher, MCP servers, notification hook, skills |
| `aify-env` | run the host tier in the current directory (the directory is the allowed workspace root) |
| `aify-env attach <agent>` | take over an agent's terminal; `Ctrl+]` detaches and leaves it running |
| `claude-aify --aify-agent <id>` | open a resident agent in this terminal (same for `codex-aify`, `hermes-aify`) |
| `aify-comms doctor` | verify what is actually running matches what you installed (`--json`, `--strict`) |
| `./redeploy.sh` | after `git pull`: re-run `install.sh` for every client installed here |

`aify-comms` itself only verifies (`doctor`, `--check`, `--version`, `--help`); any other invocation
exits 2 and points at aify-env. Inside an agent, the everyday tools are `comms_send`, `comms_inbox`,
`comms_read`, `comms_agents` and `comms_console_tail`; the full list is in
[`.claude/skills/aify-comms/SKILL.md`](.claude/skills/aify-comms/SKILL.md).

## Quick start

**If you have a coding agent, point it at this repo and ask it to install aify-comms.** It will find
`.claude/skills/aify-comms-install`, inspect host roles, clients, endpoint and versions, then show
missing/outdated/unknown items and ask only about the gaps, plus whether you want optional **herdr**.
Follow [agent-led onboarding](docs/INSTALL_ONBOARDING.md) for the install/update, verify-only and
plan-only workflows. Each repository owns its own installer; nothing silently installs another product.

**Prerequisites.**

- Service host: git, and Docker with Compose v2 (`docker compose`).
- Agent host: git, bash (Git Bash on native Windows), Node.js and npm (`install.sh` stops without
  them), network access for npm, and the runtime you install for (`claude`, `codex` or `hermes`) on
  PATH. Hermes needs Node 22 or newer; see [install.hermes.md](install.hermes.md).

By hand:

```bash
git clone <this repo> && cd aify-comms
bash scripts/install-state.sh     # what this machine already has; run it first

./setup.sh                        # service host only: generates .env + config
bash scripts/stamp.sh && docker compose up -d --build   # service :8800 (API), dashboard :8801
curl http://localhost:8800/health # {"status":"healthy"}

bash install.sh --client claude http://localhost:8800 --with-hook   # once per coding-agent client
git clone https://github.com/zimdin12/aify-env  # agent hosts only; then its own ./install.sh
```

Then, on each agent host, start `aify-env` from the directory that contains your workspaces, open
`http://localhost:8801`, go to **Environments**, fill in **Spawn Session** (environment, runtime,
agent id, workspace), click **Spawn**, and message the new agent.

**Order: service, then `install.sh --client` on each agent host, then aify-env's `install.sh`, then
start `aify-env`.** The client install writes this service into `~/.aify/services.json`, and aify-env
reads that registry once, when it starts: an aify-env started before the entry existed claims no
spawns until it is restarted, and restarting it stops its managed agents. `install.sh` asks for the
service URL when run in a terminal and otherwise uses `http://127.0.0.1:8800`; aify-env's installer
asks for the service key it is missing. Running either again is how you update. Use aify-env's own
`install.sh` rather than `npm install -g`, which cannot check the service credential.

**Starting or restarting aify-env is a deliberate action.** A second instance supersedes the first,
and the one it replaces stops its managed agents. Ask a running host with `aify-env doctor` instead of
starting one to find out.

## Agent playbooks — install, update, and verify it took effect

Every deploy path here can fail silently: the container keeps serving the previous build, a running
agent keeps the bridge code it loaded at startup, an agent launched without `--aify-agent` works but
has no status. So a flow is done when `aify-comms doctor` says `ok: true`, not when nothing errored.

| flow | do | done when |
|---|---|---|
| install a client | `bash install.sh --client <runtime> http://<service>:8800 --with-hook` | doctor `bridge-installed` green, then every agent that was running before the install relaunched |
| install / update the service | `git pull && bash scripts/stamp.sh && docker compose up -d --build` | doctor `service` reads `build <sha> == repo HEAD` (`/health` alone does not say which build) |
| update clients after `git pull` | `./redeploy.sh` (or `install.sh` per client) | `bridge-installed` green, then every agent that was running before the install relaunched |

`bridge-current` names any registered agent still reporting an older bridge build, and reads
`unknown` until agents report one; on Linux `bridge-running` also names running bridges started
before the install.

Rules that cost real hours:

1. **Installing does not reload a running agent.** Restart agents after a bridge change;
   `--resume <handle>` keeps the conversation.
2. **Launch a registered agent with its id** (`--aify-agent <id>`), or it has no status.
3. **A dead managed worker's console holds the answer.** `comms_console_tail(agentId=...)` returns
   the last recorded output with the fatal line first, marked `NOT LIVE`.
4. **After updating Hermes itself**, re-run `install.sh --client hermes`: a hermes update deletes the
   prebuilt web bundle its console needs.

Some doctor rows watch the live fleet rather than an install and can turn red on a quiet day:
`tier-version`, `env-code-currency`, `spawn-queue`, `session-handles`, `context-window`,
`managed-orphans`, `gateway-orphans`, `env-processes`, `claude-login`, `usage-openai`,
`api-exposure`, `external-keys`, `client-api-key`. Each one reports and never acts; its `detail`
says what it found and its `fix` what to do (`aify-comms doctor --json` prints both).

## Security

By default the service has **no API key**, **CORS `*`**, and publishes its port on **`0.0.0.0`** — a
trusted-LAN setup. Anything that can reach port 8800 can drive every agent, including typing into
consoles.

Turn on a key:

```bash
bash install.sh --client claude http://localhost:8800 --with-api-key   # generates or reuses API_KEY in .env
docker compose up -d                                                   # the service reads it at startup
```

Re-run `install.sh` for every other client (it reads the key from `.env`), then open the dashboard
once as `http://localhost:8800/?api_key=<key>`; the key becomes an `HttpOnly` cookie. An existing key
is never rotated, because a new one would lock out every client already installed.

Always on: a request or WebSocket from a page on another site is refused, so a web page you visit
cannot drive the fleet. aify-env binds `127.0.0.1` only and refuses browser requests.

**Agents on another machine** send here without registering. Give that machine its own key rather
than yours: add `EXTERNAL_KEYS=pc2:<key>` to `.env` (`openssl rand -hex 32`), restart, and have the
other machine send with it:

```bash
curl -X POST http://<this-host>:8800/api/v1/messages/send -H "X-API-Key: <pc2 key>" \
  -H "Content-Type: application/json" \
  -d '{"from_agent":"pc2-manager","to":"<local agent>","subject":"hi","body":"...","origin":"<its address>"}'
```

Its messages show `external: pc2`, and that name is proven by the key. The `origin` is what it said
about itself. The key can only send messages, and cannot send as an agent that lives here. It needs
`API_KEY` set; `/health` reports `externalKeys.enforced`.

**The operator key** lets the dashboard delete other agents' messages, channels and shared files. It
also stops a message you send *as* an agent from counting as that agent being present. Leave
`OPERATOR_KEY` empty and one is generated on first start into its own volume, which the dashboard
reads.

A key does not change the bind address or CORS: bind `127.0.0.1:8800:8800` in
`docker-compose.yml` if the LAN should not reach it, and set `CORS_ORIGINS` in `.env` to the
dashboard origins you use (comma-separated, for example `http://localhost:8801`), then
`docker compose up -d`. `.env` overrides `config/service.json`, and the default `.env` sets
`CORS_ORIGINS=*`. With a key set, `/health`, `/ready`, `/version`, `/docs`, `/redoc` and
`/openapi.json` still answer without one; `/ws` checks the key itself. Details:
[KNOWN_ISSUES.md](KNOWN_ISSUES.md).

## Notifications

Both are off by default, and only messages addressed to you (`to: dashboard`) or channels you joined
notify; repeats from one sender on one subject coalesce to one alert per 90 seconds.

- **Desktop:** enable notifications in the dashboard and grant browser permission. `localhost` works
  over plain HTTP; a LAN address needs the HTTPS proxy (`docker compose --profile https up -d`),
  which serves `https://<host>:8443` (`HTTPS_PORT` in `.env`).
- **Phone:** set `AIFY_NTFY_URL=https://ntfy.sh/<private-topic>` in `.env`, run
  `docker compose up -d`, and subscribe to the topic in the ntfy app. The topic URL is a credential;
  keep it in `.env`. `curl -s localhost:8800/health | jq .ntfy` shows whether alerts are going out.

The HTTPS proxy signs with its own local CA, so browsers warn until you trust that CA once per
device:

```bash
docker cp "$(docker compose ps -q https-proxy)":/data/caddy/pki/authorities/local/root.crt ./aify-root.crt
```

| where | how |
|---|---|
| Windows | `certutil -addstore -f ROOT aify-root.crt` in an admin shell |
| macOS | `sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain aify-root.crt` |
| Linux | copy to `/usr/local/share/ca-certificates/`, then `sudo update-ca-certificates` |
| Firefox | Settings → Privacy & Security → Certificates → Authorities → Import |
| Android / iOS | install the file as a CA certificate |

A hostname or IP that is not listed in `HTTPS_SITES` (`.env`) still warns after the CA is trusted; add
it there and restart the proxy.

## Managed and resident agents

- **Managed** (the normal team): spawned from the dashboard, owned by the aify-env that started them,
  restarted, stopped, compacted or reset from the dashboard. An idle managed agent is started
  automatically when you message it. `online` means a live worker is ready to claim work.
- **Resident**: a terminal you opened with `claude-aify --aify-agent <id>` (or `codex-aify` /
  `hermes-aify`). Add `--shared` so aify-env owns the terminal and it survives closing the window.
  One instance of an agent runs per host; starting one by hand replaces the running instance.
- Switch an agent between the two with **Switch to managed** / **Switch to resident**, in its details
  drawer or on its row in **Sessions**.

Agent statuses, delivery paths per runtime, compaction, handle repair and the runtime settings are
described in [docs/OPERATING_MODES.md](docs/OPERATING_MODES.md). Setup for remote hosts and workspace
roots is in [docs/BRIDGE_SETUP.md](docs/BRIDGE_SETUP.md); Hermes specifics in
[install.hermes.md](install.hermes.md).

## Versions

`VERSION` is the single release version. The dashboard header shows the running build and turns into
a warning when the checkout is behind `origin/main`; `GET /version` and `aify-comms --version` report
the same. Updating is always manual: `git pull`, `bash scripts/stamp.sh && docker compose up -d --build`,
`./redeploy.sh`, then relaunch the agents that were running.

## Repository

| path | what |
|---|---|
| `service/` | FastAPI service, SQLite schema, dashboard (`service/new_dashboard/`). Rebuild the container after changes. |
| `mcp/stdio/` | host-side MCP servers loaded by agents. Re-run `install.sh` and restart agents after changes. |
| `install.sh`, `redeploy.sh` | client installer and its update helper |
| `.claude/skills/`, `.agents/skills/` | agent skills (usage, debug, install), mirrored for Codex |
| `docs/` | design and reference; [docs/README.md](docs/README.md) says which documents are current |

Start with [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) before changing code, and
[docs/TARGET_ARCHITECTURE.md](docs/TARGET_ARCHITECTURE.md) for where it is heading.
[DECISIONS.md](DECISIONS.md) records why things are the way they are, [KNOWN_ISSUES.md](KNOWN_ISSUES.md)
what is still wrong, [docs/UNINSTALL.md](docs/UNINSTALL.md) how to remove it.

## License & contributing

MIT — see [LICENSE](LICENSE). Issues and questions welcome; for larger PRs please open an issue first
to discuss direction.
