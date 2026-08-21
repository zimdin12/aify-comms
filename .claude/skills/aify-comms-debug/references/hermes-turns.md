# aify-comms debug: Hermes turns, tools, status and native fallback

## Hermes `mcp test` works, but live turn has no aify tools

**Symptom.** Inside `hermes-aify`, `hermes mcp list` shows `aify-comms`
enabled and `hermes mcp test aify-comms` discovers `comms_register`,
`comms_send`, and `comms_agent_info`, but the model turn still cannot call
`mcp_aify_comms_comms_register`, `mcp_aify_comms_comms_send`, or
`mcp_aify_comms_comms_agent_info`.

**Cause.** `hermes mcp test` is a fresh CLI process. The visible
`hermes-aify` terminal is driven by a separate `hermes dashboard`
gateway process (pre-0.15.1 this was `hermes dashboard --tui`; `--tui`
moved to a top-level flag in 0.15.1 and the `dashboard` subcommand now
rejects it — see the gateway-host entry below), and older wrapper/plugin builds did not run
`discover_mcp_tools()` before that gateway built the TUI `AIAgent`. The gateway
could therefore have only built-in tools even though the standalone MCP test
passed.

**Fix.** Update aify-comms, run `./install.sh --client hermes`, and restart
`hermes-aify` so it loads the current `integrations/hermes-aify-plugin` shim.
The plugin now runs MCP discovery before the TUI agent is built. For an
already-open session on a current wrapper, reload the gateway MCP registry
instead of using terminal/Node/curl/direct HTTP registration. Direct HTTP
registration can still corrupt bridge-backed resident metadata.

If the prefixed tools are exposed and `mcp_aify_comms_comms_register` succeeds
but `comms_agent_info` still reports `Wake mode: hermes-missing-handle`, the
dashboard-gateway MCP child probably registered without
`runtimeConfig.gatewayUrl`. Update/redeploy again and restart `hermes-aify`:
current wrappers export `AIFY_HERMES_PORT` before the dashboard starts, and the
Hermes plugin injects `AIFY_HERMES_GATEWAY_URL` inside
`hermes_cli.web_server` so dashboard-side MCP calls can self-register live.

## Hermes fails immediately with `'NoneType' object is not iterable`

**Symptom.** A freshly restarted `hermes-aify` terminal reaches the Hermes TUI,
but an ordinary prompt such as "read aify-comms skills again and re-register"
fails before tools run:

```
API call failed: TypeError
Provider: openai-codex
Error: 'NoneType' object is not iterable
```

The wrapper may already be healthy: process list shows
`hermes.exe --tui --resume <id>` and
`~/.local/state/aify-comms/hermes-aify-active-session-<port>.json` exists.
On native Windows, current installs run `hermes-aify.cmd` through a generated
PowerShell shim, not Git Bash, so Hermes' Node TUI keeps a real console TTY.
If `hermes-aify --resume <id>` exits with `hermes-tui: no TTY`, redeploy the
Hermes wrapper and verify `C:\Users\dev\.local\bin\hermes-aify.cmd`
calls `hermes-aify.ps1`.

**Cause.** This is a Hermes/OpenAI SDK Responses streaming edge case, not an
aify registration error. ChatGPT Codex can stream valid `response.output_item.done`
function-call items and then finish with `response.completed.response.output`
set to `null`; OpenAI SDK 2.24.0 raises this local `TypeError` before Hermes
gets to call MCP tools. Current `install.sh --client hermes` installs the
`hermes-aify` runtime shim (`integrations/hermes-aify-plugin`) and the wrapper
loads it with `AIFY_HERMES_PLUGIN=1` / `PYTHONPATH`. The shim handles this
exact SDK failure in memory: it falls back to Hermes's lower-level
`create(stream=True)` path and rebuilds `response.output` from the
already-streamed items.

**Fix.** Pull/update aify-comms, run the Hermes client install/redeploy, then
restart the affected `hermes-aify` terminals so the Python process imports the
shim. If the same error repeats after restart, check the active
dashboard log at:

```
~/.local/state/aify-comms/hermes-aify-dashboard-<port>.log
```

and verify the wrapper loads the plugin:

```
head -80 ~/.local/bin/hermes-aify | grep AIFY_HERMES_PLUGIN
```

For A/B testing upstream Hermes without the shim, launch with
`AIFY_HERMES_DISABLE_PLUGIN=1 hermes-aify`. The old in-place source patch path
is legacy/debug only: set `AIFY_HERMES_LEGACY_SOURCE_PATCH=1` before
`install.sh --client hermes`.

## Managed hermes never shows `working` during a turn

**Symptom.** A managed hermes (visible-TUI) agent runs a turn but the dashboard
never shows `working` — it stays `online`/`online · awaiting reply`.

**Cause.** `hermes-managed-host.js` delivers via `prompt.submit` while idle or `session.steer` while busy; both resolve on accept, not turn completion. The old code pulsed
`turn_busy=true` then cleared it in a `finally` immediately after submit — so
working flipped 1→0 while the turn was only just starting. (The blocking
`hermes-channel.js` path is fine — its `chatStream` runs the turn to completion
before clearing.)

**Fix (2026-05-31, refined 2026-06-02 `2216c44`).** On a successful submit the loop
leaves `turn_busy` set rather than clearing it in a `finally`, so `working` reflects the
real turn. As of 2026-06-02 turn-state is driven by a **continuous, bidirectional
gateway-status detector** (`mcp/stdio/hermes-gateway-turn-detector.js` in `runDeliveryLoop`):
gateway session `working` → `/turn-start`, gateway session `idle` SUSTAINED (≥3 ticks ≈ 9s,
DEBOUNCED) → `/turn-end`. The DEBOUNCE matters here: the hermes gateway `session["running"]`
flag flips False MID-TURN (between tool calls / generation gaps), so the earlier
single-idle-read clear false-cleared `turn_busy` mid-turn → a `working`↔`online` FLAP.
Requiring N consecutive idle reads (any `working` read resets the streak) means a momentary
mid-turn idle blip can never clear the turn; the same debounce was applied to the in-flight
re-pulse probe. STATUS is pure-event (the seconds window no longer decides `working`); the
staleness window is the 30-min `TURN_BUSY_BACKSTOP_SECONDS` ceiling for a DROPPED end-event.
Explicit `queueIfBusy` instead holds on raw `turn_busy=1` until the authoritative end-event. This is BRIDGE code: it
activates when the managed hermes agent's delivery loop respawns (relaunch its
`hermes-aify`, which kill-priors the old loop and loads the new bridge file). A loop
still claiming under the machine-global `hermes-managed-host-<machine>` id (no
`-<agentId>` suffix) is running old code.

## Hermes agent shows `online` while working

**Symptom.** A hermes agent is clearly mid-turn (TUI streaming, tools running)
but the dashboard/`comms_agent_info` reads `online`, not `working`.

**Cause.** Hermes turn detection used to be purely dispatch/hook-based: aify only
saw a turn via (a) an aify dispatch run, or (b) the `pre_llm_call` turn-start hook.

**Fix / status (2026-06-02, `2216c44`).** STATUS is pure-event (the event decides
`working`, not a window). For **managed** hermes this is resolved end-to-end by a
**continuous, bidirectional gateway-status detector** (`mcp/stdio/hermes-gateway-turn-detector.js`,
wired into `runDeliveryLoop`) — the hermes mirror of the claude transcript detector. It
reads the gateway session `status` (`session.active_list` → `working`/`idle`) every ~3s
for the WHOLE delivery-loop lifetime, NOT just inside a dispatch's in-flight window:
gateway `working` edge-triggers `/turn-start` (SET), gateway `idle` SUSTAINED (≥3
consecutive ticks ≈ 9s; tune via `AIFY_HERMES_GATEWAY_TURN_IDLE_DEBOUNCE`) POSTs
`/turn-end` (CLEAR). Because it runs continuously it covers dispatch, channel-woken,
**autonomous, AND direct-typed-in-the-TUI** turns (the old #172 "working but shown
online"), and because `working` is set off the live gateway-running truth there is **no
15-min in-flight-window (`REPULSE_WINDOW_MS`) cap** dropping a still-running long turn to
`online`. It keys ONLY on the gateway's own session truth (anti-feedback-loop), never the
aify server's derived status. The dispatch delivery pulse + in-flight re-pulse remain the
instant path; this detector is the continuous backstop in both directions; the staleness
window is the 30-min `TURN_BUSY_BACKSTOP_SECONDS` dropped-event ceiling. Relaunch
`hermes-aify` to load it. **Correction (2026-06-18 audit): resident hermes DOES arm this same
detector** — `startHermesGatewayTurnDetector` runs for resident hermes whenever
`AIFY_HERMES_GATEWAY_URL` is set (`server.js`), so an operator-launched `hermes-aify` with its
gateway URL exported (the normal wrapper path) gets the same continuous bidirectional turn
detector as managed. Hermes still exposes no upstream turn-END *hook* and the bridge's
bidirectional transcript turn-state detector keys on the *claude* transcript only, so the
gateway detector is the resident hermes turn-end mechanism. The residual is only a
**gateway-less** resident hermes (no `AIFY_HERMES_GATEWAY_URL`): it has no turn detector and
self-heals off `working` at the 30-min ceiling — but with no usable wake handle (wake-mode
`*-missing-handle`) it reads `offline`, not `available`, anyway — see KNOWN_ISSUES.md (#172).
No action needed if delivery itself works.

## EVERY managed-hermes dispatch fails "Queued >180s … up-but-deaf" (gateway host died — hermes 0.15.1 `--tui`)

**Symptom.** Every dispatch to a managed hermes agent fails with `Queued for >180s with
no live claimer … up-but-deaf or never started a worker`. The agent shows `available`;
the dashboard Console briefly flashes `[terminal attached pid=…]` then closes; the env-bridge
terminal logs only `[aify] spawned managed agent …` and nothing more; the terminal row ends
`reconciled_managed_ghost_console_dead_worker`.

**Cause (hermes 0.15.1, 2026.5.29).** Hermes 0.15.1 moved `--tui` to a **top-level** flag, so
the `dashboard` subcommand now **rejects** it (`error: unrecognized arguments: --tui`). The
bridge's `ensureGatewayHost` (`mcp/stdio/hermes-managed-host.js`) launched the gateway host as
`hermes dashboard --tui --port <P> --host 127.0.0.1 --no-open --skip-build`, which arg-errored
and died instantly → `ensure-host` 60s readiness timeout → wrapper `exit 1` → PTY closes → no
channel-sidecar claimer ever registers → the run is reaped as "no live claimer". The child's
stderr was `stdio:"ignore"`, which silently hid the arg error.

**Fix (`a363822` + correction `34bca11`/`b591a28`).** Dropped the rejected `--tui` flag from the
gateway-host args. ⚠ The `a363822` claim that plain `hermes dashboard` *"serves a working
`/api/ws`"* was WRONG — it only verified the index TOKEN, not the socket. `--tui` ALSO enabled the
dashboard EMBEDDED-CHAT feature that gates `/api/ws`: `web_server.py` closes `/api/ws` with code
**4403** when `_DASHBOARD_EMBEDDED_CHAT_ENABLED` is false, and that flag is set ONLY by `--tui` OR
the `HERMES_DASHBOARD_TUI=1` env. So after the `--tui` drop the gateway served the index (readiness
probe passed) but its `/api/ws` CLOSED → **"gateway websocket connection failed"** across ALL
managed hermes agents → TUI never attached → headless orphans (operator incident 2026-06-04).
**Real fix:** set `HERMES_DASHBOARD_TUI=1` in the gateway-host spawn env (crash-safe env equivalent
of the rejected flag — verified: `/api/ws` OPENs with it, CLOSEs without). **⚠ BINARY-DEPENDENT — CORRECTED
2026-06-05 (peer review):** that OPEN/CLOSE result held on the *pre-patch* 0.15.1 binary. A later hermes
0.15.1 patch (upstream `cae6b5486`, shipped by the same update that wiped `web_dist`) hardcodes
`_DASHBOARD_EMBEDDED_CHAT_ENABLED = True` and drops the gate — on the CURRENT binary plain `hermes dashboard`
serves `/api/ws`, and `HERMES_DASHBOARD_TUI=1` is a harmless no-op kept only as a fallback for older/pinned
hermes. The durable binary-agnostic guard is the `b591a28` `/api/ws` readiness probe. **Hardening (`b591a28`):**
`ensureGatewayHost` now opens `/api/ws` (not just the index) before declaring ready on the CLI
`ensure-host` path, so a regression of this class fails FAST at spawn instead of becoming a headless
orphan (env-gated `AIFY_HERMES_VERIFY_WS`, default on). The gateway child's stderr logs to
`~/.local/state/aify-comms/hermes-gateway-host-<port>.log`. **Deploy:** `git pull`,
`./install.sh --client hermes`, relaunch the agent's `hermes-aify` (or re-send — the env bridge
invokes the fixed managed-host.js fresh per spawn). NOTE: the visible `hermes --tui` TUI flag is
unchanged — only the hidden gateway-host launch dropped `--tui` and gained the env.

**Second cause of the SAME symptom (2026-07-02): hermes itself fails to boot after update
drift.** A stale hermes checkout (operator's was 1959 commits behind, then a partial
`hermes update`) crashed at launch — first `Installing TUI dependencies… TUI build failed /
npm error Missing script: "build"` → wrapper exits → every managed hermes agent bounces
`up-but-deaf` while claude teammates on the same bridge work fine (the hermes-only spread is
the tell). The manager-side signature: `[NOT DELIVERED]` mirrors for ALL hermes teammates at
once. **As of 2026-07-08 (`195357d`) the bridge fails FAST on this signature** with a distinct
error ("hermes gateway host on port N FAILED its boot-time 'Installing TUI dependencies' npm
step …") instead of the old opaque `did not become ready within 60000ms` — so the log now
names the cause directly. (Older builds surfaced only the 60s readiness timeout.) The per-spawn
gateway stderr log is truncated each boot, so a fixed relaunch is never false-aborted by the
prior failure's signature lingering in the tail. **Triage FIRST, before blaming dispatch:** run the managed
launch by hand — `HERMES_DASHBOARD_TUI=1 hermes dashboard --port 9199 --host 127.0.0.1
--no-open --skip-build` — a healthy hermes prints `HERMES_DASHBOARD_READY port=9199` within
seconds. **Recovery:** fix hermes itself (`hermes update` / force-update the checkout), verify
the command above, then restart each affected worker (dashboard Sessions → Restart, or
`POST /api/v1/sessions/{id}/control {"action":"restart"}`). No aify reinstall is needed —
hermes updates and `install.sh` write disjoint files; reinstall only if the hermes CLI
*interface* changed (as in the 0.15.1 `--tui` case above).

## Hermes native fallback ACP persistent session

Managed Hermes defaults to a wrapper-backed `hermes-aify` PTY that delivers through the visible-session gateway bind path. This section applies only when wrapper-backed delivery is disabled/unavailable or when the Console command is `aify://virtual-rpc/hermes`: the native fallback keeps a long-lived `hermes acp --accept-hooks` child per agent (`mcp/stdio/hermes-session.js`). Some symptoms specific to this path:

### Dispatch sits at `[hermes] thinking...` forever

`hermes acp` is alive but the prompt never resolves. Cause: hermes deadlocked inside the agent loop (provider stall, hook race, etc.), or a `session/request_permission` callback is being mishandled.

**Fix.** From the dashboard, Stop the agent's persistent worker (terminal Stop) and re-dispatch — the bridge will spawn a fresh `hermes acp`, run `initialize` + `session/new` again, and continue. The synthesized terminal row survives across the restart.

### `hermes acp handshake timeout (45000ms)`

The bridge spawned `hermes acp` but never got an `initialize` response within 45s.

- Confirm `hermes acp --check` exits 0 from the host (`pwsh: hermes acp --check`). If it prompts about shell-hook approval, your install is missing the `--accept-hooks` flag — the bridge passes it by default, but a custom `AIFY_HERMES_ACP_COMMAND` may have dropped it. Re-set: `AIFY_HERMES_ACP_COMMAND="hermes acp --accept-hooks"`.
- Check stderr tail in the dispatch_event for the run — the handshake-timeout error includes the last 200 chars of hermes's stderr. Common culprits: provider credentials missing, `~/.hermes/.env` not loaded, hooks-approval prompt blocking startup.

### Bridge declines hermes's terminal/* callbacks

If hermes asks the bridge to spawn a child process (`terminal/create`, etc.), the bridge replies with method-not-found by design (no in-bridge sandbox). Hermes should fall back to its own sandbox. If hermes errors out instead, configure hermes itself with a sandbox provider — the bridge will not host tool subprocesses.
