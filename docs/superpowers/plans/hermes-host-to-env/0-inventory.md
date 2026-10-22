# 0. What aify-comms runs on a hermes host today

Measured 2026-10-22 on comms main 9f887c10, aify-wrapper main 3a14911 and aify-env main ad07615. It was read only:
nothing was started, stopped or written.

## The specimens

- **13 hermes definitions** in `~/.aify/agent-definitions`: 8 managed, 5 resident.
- **4 agents running.** Two are managed (comms-senior-dev, golf-senior-dev) and two are resident
  (dashboard-senior-dev, pc-manager).
- **Each running agent has the same three processes**, residents included:
  - a hidden `hermes dashboard --port <P> --host 127.0.0.1 --no-open --skip-build` gateway;
  - `nohup node ~/.aify-comms/mcp/stdio/hermes-managed-host.js run <agent>`, the delivery loop;
  - the visible `hermes --tui [--resume <id>]`.
- **The gateway and the delivery loop both run aify-comms code.** They are copied to the host by aify-comms'
  `install.sh` and started by aify-wrapper's `hermes-aify` launcher.

## The comms code on the host

28 hermes-named files in `mcp/stdio` (7,693 lines), plus the 594-line python plugin in
`integrations/hermes-aify-plugin`. Grouped by what each does, with the class from the harness-knowledge doc:

| what | where | class | owner after the move |
|---|---|---|---|
| start, prove and tear down the gateway | `hermes-gateway.mjs` (`ensureGatewayHost`, `teardownGatewayHost`, `maybeReEnsureGatewayHost`), `hermes-managed-host.js` (`runEnsureHostCli`) | PROCESS_OWNER | aify-env |
| stop and reap a previous generation | `hermes-daemon.js` (`stopDaemon`), `hermes-daemon-cli.js`, `hermes-prior-reap.mjs` (`reapPriorHermes`) | PROCESS_OWNER | aify-env |
| gateway port and markers in TEMP | `hermes-endpoint.js` (`agentPort`, `resolveGatewayPort`, `writeGatewayUrlMarker`, `writeSessionIdMarker`) | PROCESS_OWNER | aify-env |
| gateway JSON-RPC and ACP framing | `hermes-gateway-protocol.js`, `hermes-acp-protocol.js`, `hermes-session-id.js` | PURE_DRIVER | aify-wrapper |
| find and keep the agent's session | `hermes-active-session.mjs`, `hermes-session-effort.mjs` | PURE_DRIVER | aify-wrapper |
| detect turn edges from the gateway | `hermes-gateway-turn-detector.js`, `hermes-turn-detector-callbacks.mjs`, `hermes-inflight.mjs`, `hermes-turn-repulse.js` | mixed | status: retired by S4; delivery gating: aify-wrapper |
| claim, deliver, report runs | `hermes-delivery-loop.mjs` (`runDeliveryLoop`), `hermes-delivery-run.mjs`, `hermes-run-reporting.mjs` | COMPOSITION | aify-wrapper's hermes comms module (page 3) |
| the in-session MCP child's hermes paths | `adapters/hermes.js`, `controllers/hermes-*.js`, `hermes-session.js`, `hermes-managed-gateway-session.js` | mixed | aify-wrapper |
| gateway liveness probe | `hermes-gateway-liveness.js` | PROCESS_OWNER | aify-env |
| the python shim loaded into hermes | `integrations/hermes-aify-plugin` (`bootstrap.py`, `gateway_patch.py`, `patches.py`) | PURE_DRIVER | aify-wrapper |
| install steps | `install.sh`: `copy_hermes_assets`, `prebuild_hermes_web_dist`, `patch_hermes_codex_stream_none_fallback`, `install_hermes_plugin`, `install_hermes_config`, `install_hermes_turn_hooks` | installer | aify-wrapper's install, called by the chain |
| the gateway-orphans doctor row | `gateway-orphan-check.mjs` | host check | aify-env doctor (D11: each doctor checks its own tier) |

"mixed" means the file holds more than one class. Each is split at the symbol level before it moves, and nothing
moves as a whole file. That is page 2's first step.

## What the launcher calls today

`hermes-aify.sh.in` reaches into `@@HERMES_STDIO_DIR@@`, which is aify-comms' bridge copy:
- `hermes-managed-host.js ensure-host`, `run` and `resolve-session`;
- `hermes-daemon-cli.js stop`;
- `hermes-endpoint.js`, for the port and the markers;
- `agent-for-handle.mjs`, for the bare-resume lookup;
- `server.js`, imported once to warm the MCP child.

It also loads the python shim from `@@HERMES_PLUGIN_PATH@@`. Each of these is a seam the slices replace.

## What aify-env has today

- No gateway hosting.
- `lib/plugins/aify-comms/agent-detection/hermes.json` and `background-shell.mjs`: screen rules.
- `lib/actual-model.mjs`: reads hermes' `state.db`.
- `managed-orphans-check.mjs`: recognises a delivery loop by its command line, `hermes-managed-host.js run`.
  That match must learn the new process shape in H3, or it misses orphans.
