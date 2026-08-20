# Install For Hermes

Use aify-comms when you want dashboard-driven coordination for Hermes Agent:
live direct messages, channels, shared artifacts, active dispatch, managed
agent spawn, browser Console, and environment control.

## Prerequisites

- **The aify-comms service must be running** before these steps mean anything: they install a CLIENT
  and point it at an address. On a fresh machine, in the checkout you cloned, run `./setup.sh`, then
  `docker compose up -d --build`, then confirm `curl http://localhost:8800/health` answers before
  going further. Clone this repo first — the client install runs from the same checkout. If the service
  already runs elsewhere, use that address instead of `localhost`.
  Detail in [README.md](README.md).
- **Node.js 22 or newer** is required on the runtime that launches `hermes-aify`.
  The Hermes TUI's gateway client uses the global `WebSocket` constructor, which
  is only available in Node 22+. Earlier Node versions surface as
  `gateway exited` in the TUI when launching through `hermes-aify`. Plain
  `hermes` still works on older Node because it spawns a stdio gateway instead
  of attaching to a WebSocket.
  - WSL/Linux: install via [nvm](https://github.com/nvm-sh/nvm) (`nvm install --lts`)
    or NodeSource (`curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt-get install -y nodejs`).
  - Native Windows: install from [nodejs.org](https://nodejs.org/) or via `winget install OpenJS.NodeJS.LTS`.
- **Hermes Agent** itself, installed and on `PATH` as `hermes`.
- **The `codex` CLI, signed in — REQUIRED for the OpenAI/ChatGPT quota panel** (optional for everything else).
  Hermes does **not** hold its own OpenAI token: on a default install its `auth.json` is a *pointer*
  (`{"active_provider": "openai-codex"}`, no tokens) because it **delegates OpenAI auth to the codex CLI's
  store**. So without codex installed and logged in (`codex login`), there is no token to read anywhere and
  the dashboard's *OpenAI · ChatGPT (Codex + Hermes)* card cannot show live usage. Messaging, dispatch and
  status are unaffected.

  `install.sh` now checks this for you and prints a verdict — it does not just look for the file, it
  **proves the connection** (an expired token passes a file check and fails for real):

  ```
  [usage] OK — OpenAI/ChatGPT usage is connected.
  ```
  ```
  [usage] WARNING — OpenAI/ChatGPT usage will NOT appear in the dashboard: no OpenAI token found.
  [usage] Install the codex CLI and sign in (`codex login`). Hermes delegates its OpenAI auth to the
          codex store, so codex is what actually holds the token — a hermes-only install has none.
  [usage] Everything else works; only the OpenAI quota panel is affected.
  ```

  A found-but-expired token reports `WARNING … the ChatGPT usage API rejected it (HTTP 401) … re-authenticate
  with codex login`. The check never fails the install (usage is advisory). For scripted/agent installs, run
  `node ~/.aify-comms/mcp/stdio/usage-preflight.js --json` for a machine-readable
  `{ok, code, message, detail}` — `code` is one of `ok` / `no-token` / `rejected` / `unreachable`.

  Token discovery is **not** OS- or layout-dependent: every known codex/hermes store is searched
  (`~/.codex`, `~/.hermes`, `%LOCALAPPDATA%\…`, `~/.config/…`, macOS *Application Support*), and
  `CODEX_HOME` / `HERMES_HOME` win if you use a non-default location.

> **Path style is decided at install time.** `install.sh` detects whether the
> `hermes` it wraps is a Linux binary (WSL/native Linux) or a native Windows
> binary, and bakes the matching path style for the plugin `PYTHONPATH` and the
> MCP `server.js` arg. If you later switch which Hermes `hermes-aify` should wrap
> (e.g. change `AIFY_HERMES_COMMAND`/`HERMES_COMMAND`/`PATH` from Linux Hermes to
> native Windows Hermes or vice versa), **re-run `install.sh --client hermes`**
> so the wrapper and config paths match the new runtime.

> **Re-run `./install.sh --client hermes` after EVERY Hermes update.** A Hermes
> upgrade wipes the prebuilt `hermes_cli/web_dist` UI bundle, and the managed
> gateway host runs `hermes dashboard --skip-build`, which then dies with
> `FileNotFoundError: .../web_dist/index.html` (operator-visible as the gateway
> "installs deps then closes"). The installer's web_dist prebuild (see "web_dist
> prebuild" below) restores the bundle, so reinstalling after each Hermes update
> is required to keep managed/resident hermes dispatch working.

## Copy-Paste Install

Install Hermes first:

```bash
curl -fsSL https://raw.githubusercontent.com/NousResearch/hermes-agent/main/scripts/install.sh | bash
```

Then install aify-comms into Hermes:

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/aify-comms
cd ~/aify-comms
bash install.sh --client hermes http://localhost:8800 --with-hook
```

If you are using local-only mode with no shared server:

```bash
git clone https://github.com/zimdin12/aify-comms.git ~/aify-comms
cd ~/aify-comms
bash install.sh --client hermes --with-hook
```

Restart Hermes after install.

The installer verifies that the copied `node-pty` package can load its native binary and automatically rebuilds it when the package exists but the binary is missing or unloadable. Use `aify-comms doctor --json` after installation; checking only `node_modules/node-pty` is not sufficient proof that managed Console PTYs can start.

The installer writes the MCP entry and optional hook to Hermes' active config
home. On native Windows this is often `%LOCALAPPDATA%\\hermes` (for example
`C:\\Users\\dev\\AppData\\Local\\hermes\\config.yaml`), not
`~/.hermes`. To confirm the target before or after install:

```bash
hermes config path
hermes mcp list
```

For dashboard-managed spawns, also connect an environment bridge on the machine
that should run Hermes:

```bash
cd /path/to/workspace-or-workspace-parent
aify-comms http://localhost:8800
```

On Linux, macOS, or WSL use `aify-comms`. On native Windows from PowerShell/cmd
use `aify-comms.cmd`. The current directory is always an allowed workspace root;
extra root arguments are optional safety boundaries, not the per-agent project
choice.

If the dashboard says Hermes is unavailable even though `hermes-aify` exists,
check the underlying runtime command from the same Windows user/shell that runs
the bridge:

```powershell
Get-Command hermes
Get-Command hermes-aify.cmd
```

`hermes-aify` is only the aify wrapper; the environment bridge still needs the
real `hermes` executable. If Hermes is installed under another path, set it and
restart the bridge:

```powershell
[Environment]::SetEnvironmentVariable('AIFY_HERMES_COMMAND','C:\path\to\hermes.exe','User')
```

## Confirm it took effect

Every deploy path in this repo can fail silently: no error, everything looks installed, and what you
changed is not what is running. Do not read the absence of an error as success.

```bash
aify-comms doctor          # human-readable; --json for scripts, --strict to exit non-zero
```

On a fresh install `service`, `bridge-installed`, `skills-installed` and `wrapper-current` should all
be green. `bridge-running` and `agent-identity` SKIP on Windows — they read `/proc` — so on Windows
`bridge-current` is what tells you a running bridge is on the current build. A check that could not
gather evidence reports `unknown-all` and fails; that is the tool working, not a bug to quieten.

## Auto / bypass flag

`hermes-aify` now adds Hermes' `--yolo` flag (`HERMES_YOLO_MODE=1`) to the
interactive TUI launch **by default**, bypassing all dangerous-command approval
prompts. Pass `--safe` (or `--no-auto`) to opt OUT and KEEP normal visible
approval prompts. This mirrors `claude-aify`
(`--dangerously-skip-permissions` by default) and `codex-aify`
(`--dangerously-bypass-approvals-and-sandbox` by default). The flag is consumed
by the wrapper and applied only to the default chat/TUI launch, not to explicit
passthrough subcommands like `hermes-aify model list`.

Note: the wrapper's `--yolo` reaches only the visible TUI *client*. For MANAGED
agents the turn actually runs on the hidden gateway HOST, which does NOT inherit
the client flag — so the gateway host is spawned with `HERMES_YOLO_MODE=1` in its
env (hermes freezes YOLO at import from that var via `tools/approval.py`). That
env is what lets an unattended managed dispatch run without prompting, since no
operator is at the wheel to answer a tool-approval prompt.

## Session-mode flag

`hermes-aify` accepts `--resident` and `--managed`. Precedence: inherited
`AIFY_SESSION_MODE` env wins (bridge-spawned managed PTYs set it to `managed`);
else the flag; else TTY auto-detect via `[ -t 0 ]` — interactive defaults to
`resident`, non-TTY to `managed`. Use the explicit flag only when TTY detection
might be wrong for your shell context.

## Delivery path

Managed hermes uses the visible-TUI model: a hidden `hermes dashboard --port
<P>` gateway host (spawned with `HERMES_DASHBOARD_TUI=1` + `HERMES_YOLO_MODE=1`
in its env — hermes 0.15.1 REJECTS `--tui` and `--yolo` on the `dashboard`
subcommand, so the embedded-chat `/api/ws` socket and no-prompt YOLO are enabled
via env instead) plus a `hermes-managed-host.js run <agent>` delivery
loop (runs as a `channel-sidecar` bridge; discovers the TUI's live session by
the agent's stored **real session id** via WS `session.active_list`, and
uses WS `prompt.submit` while idle and native `session.steer` while busy; rejected or racing busy delivery requeues without falling through to `prompt.submit`), plus a VISIBLE
`hermes --tui --resume <real-session-id>` rendered in the dashboard Console via
xterm.js. The agent self-replies via `comms_send`. Session continuity uses the
agent's **native hermes session id** — a normal timestamp id, symmetric with
claude (UUID) / codex (thread). There is no synthetic `aify-<agentId>` session.

Resident and managed now share one delivery model: the RESIDENT branch uses the
SAME hidden `hermes dashboard` gateway host (spawned with `HERMES_DASHBOARD_TUI=1`)
+ background delivery loop as managed (injected messages render in the visible TUI
via gateway-WS
`prompt.submit` while idle and native `session.steer` while busy; rejected or racing busy delivery requeues without falling through to `prompt.submit`). The old per-agent `hermes gateway run`
api_server daemon resident path was DELETED — resident no longer starts or tears
down any api_server daemon. Both branches resume the agent's stored real session
id, so continuity is consistent regardless. The retired managed-delivery pieces
(the per-agent `hermes gateway run` api_server daemon AS the delivery path,
`aify.session.bind_transport` / `HermesResidentController`, and api_server `chat`
wake) should not be treated as live. The agent→real-session binding is the
per-agent marker `aify-hermes-session-<agentId>`; the bridge reads the visible
session's real id from the active-session file (env
`HERMES_TUI_ACTIVE_SESSION_FILE` / `AIFY_HERMES_ACTIVE_SESSION_FILE`), which is
now the PRIMARY id source.

If wrapper-backed delivery is disabled or unavailable, the bridge can fall
back to native Hermes controllers (`HermesController` /
`HermesManagedGatewaySession`) and synthesized `aify://virtual-rpc/hermes`
terminal output for operator visibility. That fallback does not provide the
same live TUI symmetry as wrapper mode.

### Managed launch flow (no loop health-gate; the TUI launches directly)

The managed-hermes triad is the gateway host, the background delivery loop, and
the visible TUI. The `hermes-aify` wrapper's managed flow is: **ensure the
gateway host is up → spawn the background delivery loop (capture its PID, then
kill-prior excluding that PID — the self-reap-race guard) → exec/Invoke the
visible `hermes --tui` directly.** The wrapper does NOT block the TUI on the
loop becoming a live claimer.

There is **no loop health-gate** (removed 2026-06-02). An earlier build inserted
a "health-gate" between the loop spawn and the TUI launch that polled an
`aify-hermes-loop-ready-<agent>` marker for up to 30s and could (a) fatal-exit
and refuse to start the TUI, or (b) even when demoted to non-fatal, dump wrapper
chatter into the dashboard PTY ahead of the TUI and stall the console for up to
30s. Both turned a transient loop hiccup (agent not yet registered, service
mid-restart, gateway warming up) into a dead or wrapper-spammed console, so the
gate was removed entirely from both generated wrappers (`hermes-aify` and
`hermes-aify.ps1`).

Nothing is lost by not gating: the loop keeps retrying the gateway and
`/dispatch/claim` on its own in the background, and **deliverability is reflected
server-side by the claimer-lease gate** — a managed-hermes agent reads `online`
only when the loop has actually acquired its claimer lease, and a send while the
loop is not yet a live claimer simply queues (the queued-run backstop reaper is
the safety net). The visible TUI therefore launches clean and immediately, while
status accurately reflects whether the loop is delivering.

The gateway host is **shared between the loop and the visible TUI**: the
wrapper's ensure-host spawns it for the TUI, and the loop REUSES it. The loop
never kills a reused/shared gateway — see "Restarting aify-comms is a clean
slate" below for how the gateway's lifetime ties to the TUI/console.

### Restarting aify-comms is a clean slate

The environment bridge OWNS the managed-hermes triads it spawned. Two hooks
keep a restart honest:

- **Shutdown teardown** — on graceful shutdown (and on the supersede path), the
  bridge tears down every managed session it owns: it stops the console PTYs,
  port-kills the gateway hosts, and reaps the detached delivery loops/daemons
  for its owned agents.
- **Dashboard STOP reaps the whole triad** (2026-06-02) — a dashboard **Stop**
  on a managed-hermes agent now tears down the entire triad (gateway host +
  delivery loop + daemon), agent-scoped, not just the console PTY. The stop
  control carries the target's `agentId` + runtime + sessionMode so the bridge
  recognizes a managed-hermes stop and runs an agent-scoped teardown; a resident
  hermes / claude / other-runtime stop is never touched, and another agent's
  processes are never enumerated. (STOP and Relaunch reap **synchronously**.)
- **Boot-time survivor sweep + marker sweep** — on the next env-bridge start,
  before the spawn loop comes up, the bridge sweeps for managed-triad survivors
  of a crashed/SIGKILL'd predecessor and reaps any whose owning bridge is no
  longer live in `bridge_instances`. A companion **tombstoned-marker sweep**
  deletes the `aify-hermes-{port,daemon-pid,key}-<agent>` marker files for any
  agent absent from the live `/agents` keyset (removed/tombstoned). Fail-safe:
  a still-known agent (including a co-located other-env's live agent) is never
  swept, and an unknown keyset sweeps nothing.

Both are **scoped to the agents this env bridge owns** (its `cwdRoots`) and
**never touch resident sessions or another env's agents**. The net effect:
restarting `aify-comms` is a guaranteed clean slate for managed sessions — no
orphaned gateway hosts, no zombie `hermes.exe` proliferation, even after a hard
crash. Managed sessions are re-spawned fresh by the dashboard/spawn loop, not
inherited.

The boot sweep also checks live process truth before trusting backend ownership
metadata. If a live resident wrapper exists for an agent, its associated process
family is protected even when the service still carries stale managed ownership.
This guard prevents an environment-bridge restart from killing the resident
gateway/TUI and surfacing `gateway websocket connection failed`.

The shared gateway's lifetime ties to the TUI/console, NOT to the delivery loop.
The loop kills the gateway host **only if it spawned that host itself** (an owned
child handle); it **never port-kills a reused/shared gateway** and never clears
the gateway port/key markers — those tie to the gateway and kill-prior needs the
persisted port marker to reap it on relaunch. So the gateway a managed agent
shares with its visible TUI is reaped by **kill-prior on relaunch** and the
**env-bridge survivor sweep on restart** (above), not by a transient loop exit.
This is what fixed the "gateway websocket connection failed" incident where a
loop exit (e.g. a transient 410) port-killed the gateway out from under the live
TUI and dropped the TUI's WebSocket.

**kill-prior also reaps the prior visible resume-TUI (2026-06-02).** On a silent
relaunch, kill-prior previously reaped the prior delivery loop, gateway host, and
daemon but NOT the prior `hermes --tui --resume aify-<agent>` visible TUI, so each
relaunch leaked a duplicate resume-TUI. kill-prior now also reaps that prior
resume-TUI, matched to the EXACT pinned handle (`aify-<sanitized agentId>`), never a
broad `hermes --tui`. This reap (and the gateway port-kill + daemon stop) is gated to
the **pre-spawn call only**, so the post-spawn self-reap-race call can never kill the
gateway/daemon/TUI the current launch just brought up (the 2026-06-02 port-kill root
cause behind "gateway websocket connection failed").

A managed agent whose **owning environment bridge is offline computes `offline`**
immediately — regardless of any surviving delivery-loop heartbeat — because a
managed agent can only be hosted by its owning env bridge. So killing
`aify-comms` makes its managed agents show `offline` right away, not a stale
`available`/`online`. (Resident agents are excluded: their liveness is the
resident wrapper bridge, not the env bridge.)

`hermes-aify` does NOT require the `--strict-mcp-config` + minimal-MCP
isolation that `claude-aify` needs to work around the Claude Code stdio MCP
race bug.

### Resident dispatch delivery (operator-launched `hermes-aify`)

`hermes-aify` runs the operator's real Ink terminal TUI for `hermes chat`, and it exposes a local gateway the aify-comms bridge can use for live resident dispatch. Session continuity uses the agent's **native hermes session id** (a normal timestamp id), stored as the `sessionHandle` — symmetric with claude (UUID) / codex (thread). `hermes-aify --aify-agent <id>` brings up the gateway-host and resumes the agent's stored real session (or starts fresh the first time); `hermes-aify --resume <real-session-id>` recovers the agent from the stored handle and resumes that real session. There is no synthetic `aify-<agentId>` session — the operator never types one, and `HERMES_TUI_RESUME` is no longer pinned to a derived name. The aify-comms bridge attaches to the same `/api/ws` gateway, reads the visible session's real id from the active-session file (env `HERMES_TUI_ACTIVE_SESSION_FILE` / `AIFY_HERMES_ACTIVE_SESSION_FILE`, the PRIMARY id source) and discovers it via WS `session.active_list`, then delivers via WS `prompt.submit` when idle or native `session.steer` while busy; rejected or racing busy delivery requeues without interrupting the active turn. MCP discovery still runs before the TUI gateway builds its `AIAgent`; this matters because `hermes mcp test aify-comms` runs in a separate CLI process and can succeed while the already-running TUI gateway still has no `mcp_aify_comms_*` tools.

1. The wrapper's `ensure-host` (in `hermes-managed-host.js`) spawns `hermes dashboard --port <P> --host 127.0.0.1 --no-open --skip-build` as a hidden background child, with `HERMES_DASHBOARD_TUI=1` (and `HERMES_YOLO_MODE=1`) in its env. The env sets `_DASHBOARD_EMBEDDED_CHAT_ENABLED=True` in `hermes_cli/web_server.py`, which mounts the `/api/ws` JSON-RPC endpoint at the `tui_gateway/server.py` dispatcher. (hermes 0.15.1 moved `--tui` to a top-level flag and the `dashboard` subcommand now rejects it — `HERMES_DASHBOARD_TUI=1` is the crash-safe equivalent; `ensure-host` additionally WS-verifies `/api/ws` actually OPENs before declaring the host ready.)
2. The wrapper fetches `http://127.0.0.1:<P>/` and parses the ephemeral `__HERMES_SESSION_TOKEN__` from the injected `<script>` tag (hermes' own `web_server.py`, not ours).
3. It exports `HERMES_TUI_GATEWAY_URL=ws://127.0.0.1:<P>/api/ws?token=<T>` in the env passed to `hermes --tui`, and resumes the agent's stored real session id (`--resume <real-session-id>`) when one exists, else starts fresh. The Ink TUI's `gatewayClient.ts:startAttachedGateway` opens a WebSocket to that URL instead of spawning its own stdio sidecar — operator sees their normal terminal TUI experience, resumed on the agent's native session. On native Windows, `hermes-aify.cmd` runs a generated PowerShell shim instead of Git Bash so the final `hermes.exe --tui` process keeps the real console TTY.
4. The aify-comms bridge (loaded inside `hermes chat` as an MCP server) ALSO opens a WebSocket to the same `/api/ws` (it reads `AIFY_HERMES_GATEWAY_URL` from env, written into the hermes runtime marker by `server.js`). For inbound aify-comms messages the bridge reads the visible session's real id from the active-session file (env `HERMES_TUI_ACTIVE_SESSION_FILE` / `AIFY_HERMES_ACTIVE_SESSION_FILE`, the PRIMARY id source) and confirms it via WS `session.active_list`, then issues JSON-RPC `prompt.submit` while idle or `session.steer` while busy; rejected or racing busy delivery requeues without falling through to interrupting submit. The session id is the agent's native real id (bound by the `aify-hermes-session-<agentId>` marker), so no `bind_transport` / `session.most_recent` negotiation is needed. Hermes emits real gateway events as `event` frames such as `message.delta`, `message.complete`, `tool.start`, and `tool.complete`; aify-comms translates those into run output and chat replies.

This is the Hermes equivalent to Claude Code channel delivery for the harness-console feature: the prompt and reply should render in the open `hermes-aify` terminal, while the same streamed events complete the aify-comms run/chat accounting.

Resident Hermes registration must come from the wrapper's MCP bridge. Do not
repair or create resident Hermes agents with raw `POST /api/v1/agents` scripts:
those can write `runtimeConfig.gatewayUrl`, but they cannot create the live
`bridgeInstanceId` heartbeat or the dispatch claim loop. A record in that state
is reported as `offline` and dashboard/chat sends are rejected until you restart
`hermes-aify` and run `comms_register` from the visible session, or switch the
identity back to managed.

Hermes exposes MCP tools with server-prefixed callable names. For the
aify-comms MCP server, use `mcp_aify_comms_comms_register`,
`mcp_aify_comms_comms_agent_info`, and `mcp_aify_comms_comms_send` in Hermes
turns; unprefixed names such as `comms_register` are shorthand used by generic
docs and other clients. `hermes mcp test aify-comms` listing
`comms_register` means the live callable name will be the prefixed Hermes tool
name when that toolset is exposed to the turn.

If a live `hermes-aify` turn can run `hermes mcp test aify-comms` but still
does not expose `mcp_aify_comms_comms_register` /
`mcp_aify_comms_comms_agent_info`, the active TUI gateway has not loaded MCP
tools. On current installs, restart `hermes-aify`; for an already-open gateway,
the gateway `reload.mcp` method (or the wrapper's reload-MCP control if
available) repairs the live registry without direct HTTP registration.

Hermes dashboard turns execute MCP tools inside the dashboard-gateway process,
not the later `hermes chat` child. Current wrappers export the selected
dashboard port before launching that process, and the runtime plugin derives
`AIFY_HERMES_GATEWAY_URL` inside `hermes_cli.web_server` from that port plus
Hermes' own session token. Without that dashboard-side env injection,
`mcp_aify_comms_comms_register` may be callable but still register as
`hermes-missing-handle` because no `runtimeConfig.gatewayUrl` reached
aify-comms.

**Ordinary busy sends use native `session.steer` without interrupting the active turn.** Explicit `queueIfBusy` waits for turn-end. If steer rejects or errors after the gateway was observed working, the run is requeued; it never falls through to interrupting `prompt.submit`.

**Bypass:** set `AIFY_HERMES_SKIP_GATEWAY=1` to fall back to plain `hermes` exec without the dashboard child. Use this if the dashboard probe is breaking your install and you don't need resident bridge-injection.

**Plugin A/B test:** set `AIFY_HERMES_DISABLE_PLUGIN=1` to launch
`hermes-aify` without the aify runtime shim. This is useful for comparing
upstream Hermes behavior after a Hermes update. With the plugin disabled,
resident visible-session binding and the guarded Codex stream fallback are not
provided by aify-comms. The old in-place source edit path is legacy/debug only:
set `AIFY_HERMES_LEGACY_SOURCE_PATCH=1` before running `install.sh --client
hermes` if you explicitly want that behavior.

**Cleanup:** `trap cleanup_aify_dashboard EXIT INT TERM` in the wrapper kills the dashboard child on wrapper exit, so `hermes-aify`'s lifecycle owns the dashboard process. Background dashboard logs go to `$XDG_STATE_HOME/aify-comms/hermes-aify-dashboard-<port>.log` (or `~/.local/state/aify-comms/...` on systems without XDG_STATE_HOME).

**Known limitations.** The dashboard binds to 127.0.0.1 only and uses ephemeral per-process tokens — it's safe to leave running. The `--skip-build` flag relies on hermes having already built the web UI dist once; **`install.sh --client hermes` now pre-builds this automatically** (see "web_dist prebuild" below). If you skip install.sh's prebuild (e.g. install hermes after running install.sh), you can prime it manually with `hermes dashboard --no-open` once.

### web_dist prebuild (added 2026-05-25)

`install.sh --client hermes` detects whether `<hermes-install-root>/hermes_cli/web_dist/index.html` exists and, if not, runs `npm install && npm run build` once in `<hermes-install-root>/web/`. Without this, fresh hermes installs hit the failure described in the section above: `hermes dashboard --skip-build` dies with `✗ --skip-build was passed but no web dist found at: ...`, the wrapper falls through to plain `hermes`, and every resident-channel wake for the session reports `hermes-missing-handle`.

Detection order for the hermes install root:

1. `AIFY_HERMES_INSTALL_ROOT` env (overrides everything; useful when `hermes` is symlinked to a non-canonical location)
2. `hermes config path` parsed up to `/hermes_cli/...` (the canonical Windows path is `~/AppData/Local/hermes/hermes-agent/hermes_cli/config.yaml`, so the install root is `~/AppData/Local/hermes/hermes-agent`)
3. Skip with a log line if neither resolves

The prebuild is idempotent — re-running `install.sh --client hermes` after web_dist exists logs `hermes web_dist already present at ...` and skips. **Re-run is required after every Hermes upgrade**, because the upgrade wipes `web_dist` and the gateway host then crashes with `FileNotFoundError: .../web_dist/index.html` ("installs deps then closes") until the prebuild restores it.

### Fallback warning (added 2026-05-25)

When `hermes-aify` cannot start the dashboard gateway (port allocation failure, dashboard probe timeout, missing web_dist, token capture failure) or when `AIFY_HERMES_SKIP_GATEWAY=1` is set, it now prints a multi-line WARNING block before exec-ing plain `hermes`:

```text
[hermes-aify] WARNING: AIFY_HERMES_GATEWAY_URL was NOT exported to this hermes session.
[hermes-aify]   Reason: <one of port_alloc_failed / dashboard_unreachable / token_capture_failed / gateway_disabled>
[hermes-aify]   Log:    ~/.local/state/aify-comms/hermes-aify-dashboard-<port>.log
[hermes-aify]   Effect: comms wake/dispatch to this agent will report 'hermes-missing-handle'.
[hermes-aify]   Fix:    re-run install.sh --client hermes to prebuild hermes web_dist, or
[hermes-aify]           inspect the dashboard log above for the underlying error.
```

Without this banner the fallback was silent and operators had no signal that their resident hermes wake-mode would never work. Current fallback still preserves an explicit `hermes-aify --resume <session-id>` by launching plain `hermes --tui --resume <session-id>` when the gateway path cannot start.

### Session continuity (native session id)

Session continuity uses the agent's **native hermes session id** — a normal
timestamp id stored as the `sessionHandle`, symmetric with claude (UUID) /
codex (thread). There is no synthetic `aify-<agentId>` session. `hermes-aify
--aify-agent <id>` resumes the agent's stored real session (or starts fresh the
first time); `hermes-aify --resume <real-session-id>` resumes that specific
session. The launch-side `resolve-session` step (in `hermes-managed-host.js`,
run by the wrapper before the visible TUI launches) resolves which session to
`--resume`: as of the 2026-06-04 `session_key` fix the resumed id is the
**durable `session_key`** (looked up against the SessionDB / `session.list`, so
it survives gateway and bridge restarts), NOT the ephemeral runtime sid — the
ephemeral id is dead on the next attach and would fail gateway 4007 "session not
found". (Delivery itself — `prompt.submit` — still targets the
ephemeral live sid the loop discovers via `session.active_list`; only the resume
key is the durable one.) The agent→real-session binding is the per-agent marker
`aify-hermes-session-<agentId>`, and the bridge reads the visible session's real
id from the active-session file (`HERMES_TUI_ACTIVE_SESSION_FILE` /
`AIFY_HERMES_ACTIVE_SESSION_FILE`) — this active-session-file discovery is now
the PRIMARY id source. The `session.most_recent` binding path is not used: it
reported historical Hermes DB state and could bind to a session that could not
visibly receive delivery. The aify-comms bridge confirms the live session via
WS `session.active_list`.

If dispatch says `visible session not found`, the open terminal was started
with an old wrapper, with `AIFY_HERMES_DISABLE_PLUGIN=1`, or before the visible
TUI attached to its real session. Re-run `install.sh --client hermes`, restart
that `hermes-aify` terminal, and re-register from inside the same visible
session.

## What This Installs

- The shared `aify-comms` local MCP server for Hermes.
- A Hermes MCP config entry in the active Hermes config file (`hermes config path`).
- The resident wrapper `hermes-aify`, which exports `AIFY_COMMS_URL` so shell hooks know which aify service to call and loads `integrations/hermes-aify-plugin` for Hermes runtime compatibility.
- A `pre_llm_call` shell hook (`~/.hermes/agent-hooks/aify-turn-start.sh`) that POSTs `/api/v1/agents/{id}/turn-start` before each LLM call. Hermes has no matching upstream turn-end hook, so managed Hermes and gateway-bound resident Hermes use the continuous bidirectional gateway-status detector: gateway `working` sets turn-start and sustained gateway `idle` clears it. Explicit `queueIfBusy` holds on raw `turn_busy=1` until that authoritative end-event; the 30-minute status ceiling only backstops a dropped end-event.
- With `--with-hook`, a non-blocking Hermes `post_tool_call` notification hook (separate from the turn-start hook above; this one is for incoming-message notifications).

Resident Hermes is terminal-first — `hermes-aify` opens an interactive Hermes
TUI for human use. Managed Hermes defaults to the same wrapper shape, but the
environment bridge owns the `hermes-aify` PTY and the dashboard Console renders
that real TUI. Native `HermesController` / ACP fallback remains available when
wrapper-backed delivery is disabled or unavailable.

## Native fallback ACP session (managed dispatches)

**Re-running `install.sh --client hermes` REPLACES the existing `aify-comms` block in `config.yaml` in place.** The config patcher (`_patch_hermes_config_at` in install.sh) locates the existing `aify-comms:` entry under `mcp_servers:`, splices it out, and re-inserts the freshly generated block — so new `env:` entries (e.g. env-var propagation like `AIFY_HERMES_GATEWAY_URL`) flow on reinstall, no manual edit needed. (This is a change from older builds, which exited early and left a stale block.) If the block is ever hand-corrupted you can still delete it entirely and rerun `bash install.sh --client hermes` to regenerate it.

Current managed Hermes defaults to wrapper-backed `hermes-aify` PTY delivery
(`managed_via_wrapper=["codex","hermes"]`). The bridge owns the wrapper PTY, the
wrapper starts the local dashboard gateway, and the delivery loop delivers into
the agent's real session via gateway WS. Ordinary busy sends use `session.steer`;
explicit `queueIfBusy` waits for turn-end before `prompt.submit` (a submit-time
busy race requeues). See the "Delivery path" section above — the old `aify.session.bind_transport`
negotiation is retired. The rest of this section describes the native
controller fallback used only when wrapper-backed delivery is disabled or
unavailable.

On the fallback path, the bridge spawns a single `hermes acp --accept-hooks`
per agentId on first dispatch, runs the ACP handshake (`initialize` →
`session/new`), and reuses the same `sessionId` for every subsequent
`session/prompt`. This gives native conversation continuity and token-level
streaming in a synthesized dashboard terminal, but it does not provide the
same visible TUI symmetry as wrapper-backed delivery.

- Default launcher: `hermes acp --accept-hooks` (looked up on PATH).
- Override: `AIFY_HERMES_ACP_COMMAND="/abs/path/to/hermes acp --accept-hooks"` (quote-aware, so `AIFY_HERMES_ACP_COMMAND='"C:\Program Files\hermes\hermes.exe" acp --accept-hooks'` works for paths-with-spaces)
  (or per-agent via `runtimeConfig.hermesAcpCommand`).
- Idle reaper: 24h by default. Override globally via
  `AIFY_HERMES_IDLE_TIMEOUT_MS` or per-agent via
  `runtimeConfig.hermesIdleTimeoutMs`.
- Handshake-startup window: 45s default; tune via
  `AIFY_HERMES_STARTUP_TIMEOUT_MS` or `runtimeConfig.startupTimeoutMs`.
- Resident hermes is unaffected — the operator-typed hermes still launches
  interactively under PTY.

**Filesystem callbacks are sandboxed to the agent's `cwd`.** When hermes requests `fs/read_text_file` or `fs/write_text_file`, the bridge resolves the path against the registered session `cwd` and refuses anything outside that tree with JSON-RPC error -32602. This prevents a compromised agent from reading `~/.ssh/id_rsa` or writing arbitrary host files through the ACP callback channel. Operators who genuinely need unrestricted access can set `AIFY_HERMES_FS_UNSAFE=1` to disable the containment check (explicit opt-out, not recommended).

**Permission auto-approve uses an allow-list.** The bridge auto-selects only options whose `kind` is `allow_once` or `allow_always`. If hermes presents only escalation-kind options, the bridge returns `outcome.cancelled` instead of picking option[0]. The hook lives in `_handleClientRequest` in `mcp/stdio/hermes-session.js` if you need a different policy.

To verify this fallback child is alive: open the agent's Console after a
managed dispatch whose command is `aify://virtual-rpc/hermes` — status should
go `available → working → available` while the same `hermes acp` PID stays up
between turns (`tasklist | findstr hermes` on Windows, `pgrep -f "hermes acp"`
on POSIX). A second dispatch reuses the same PID. The bridge declines
hermes's `terminal/*` callbacks (no in-bridge sandbox), so configure hermes
itself to use its own sandbox if you need tool-driven child processes.

See [docs/HERMES_INTEGRATION.md](docs/HERMES_INTEGRATION.md) for the full
integration guide, hooks details, MCP config shape, resident mode, and current
limits.

## How the install works (and updating)

`install.sh` copies the bridge runtime (`mcp/stdio` + its `node_modules`) into a native folder at `~/.aify-comms` (override with `AIFY_HOME`) and points the wrappers and MCP config at that copy — not at this repo checkout. This keeps bridge startup fast on slow/bind-mounted filesystems. Consequence: after `git pull`, changes under `mcp/stdio/` only take effect once you **re-run `install.sh`** (refreshes the copy) and restart the wrapper/bridge. Updating the runtime CLI itself (e.g. a hermes or claude update) does not require reinstalling aify-comms — the two write disjoint files.
