# Hermes Aify Runtime Plugin

`hermes-aify` loads this repo's `integrations/hermes-aify-plugin` directory with
`PYTHONPATH` and `AIFY_HERMES_PLUGIN=1`.

The plugin exists so a Hermes update does not erase aify-comms compatibility
logic under `%LOCALAPPDATA%\hermes\hermes-agent`.

## Runtime Patches

- `tui_gateway.server`: wraps the registered `prompt.submit` handler with a
  `TeeTransport` re-assert. When the delivery loop submits a turn for a
  resident/managed agent, Hermes' handler would otherwise rebind the session's
  streaming transport to the loop's socket, so the whole turn (echo, deltas,
  reply) would stream to the loop and the operator's visible TUI on the same
  session would render nothing. The patch re-asserts a tee
  (`primary=visible TUI`, `secondary=loop`) so the TUI keeps its stream and the
  loop still gets its copy. It is idempotent and a no-op when the caller is the
  visible TUI itself.
- `tui_gateway.server`: registers `aify.session.render_notice`, which asks the
  visible TUI to render a boxed `aify-comms message` transcript notice and
  status update before an externally injected prompt starts. Hermes' local
  submit path paints the user message in the frontend before `prompt.submit`;
  bridge-injected prompts need this extra render hint so the console visibly
  moves as soon as a message is received.
- `tui_gateway.server`: triggers MCP tool discovery on first agent creation, so
  gateway-spawned agents have the aify-comms `comms_*` tools available.
- `tui_gateway.server`: also registers `aify.session.bind_transport`
  defensively (resolves a visible session and tees the caller's transport).
  The current delivery loop no longer uses it — delivery resolves the agent's
  real session via `session.active_list` and uses `prompt.submit` /
  `session.steer` — but the method is still installed for compatibility.
- `hermes_cli.main`: preserves the wrapper-provided
  `HERMES_TUI_ACTIVE_SESSION_FILE` so the bridge can discover the visible
  session written by the TUI.
- `hermes_cli` web server: exposes the per-agent gateway URL/token to MCP
  children spawned by the gateway, always preferring the gateway owned by this
  dashboard process over a stale inherited `AIFY_HERMES_GATEWAY_URL`.
- `agent.codex_runtime`: guards the ChatGPT Codex Responses stream edge case
  where the final completed response has `output = null`.

The shim is enabled by default only through `hermes-aify`; plain `hermes`
launches are unaffected.

## A/B Testing

Disable the shim for a single launch:

```bash
AIFY_HERMES_DISABLE_PLUGIN=1 hermes-aify
```

That is useful after a Hermes update when comparing upstream behavior. With the
plugin disabled, the gateway `render_notice` / `TeeTransport` shim and the Codex
null-output guard are not supplied by aify-comms.

The old in-place source edit path remains for emergency debugging only:

```bash
AIFY_HERMES_LEGACY_SOURCE_PATCH=1 bash install.sh --client hermes http://192.0.2.10:8800
```
