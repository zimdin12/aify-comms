# Hermes Aify Runtime Plugin

`hermes-aify` loads this repo's `integrations/hermes-aify-plugin` directory with
`PYTHONPATH` and `AIFY_HERMES_PLUGIN=1`.

The plugin exists so a Hermes update does not erase aify-comms compatibility
logic under `%LOCALAPPDATA%\hermes\hermes-agent`.

## Runtime Patches

- `tui_gateway.server`: registers `aify.session.bind_transport`, which binds
  the bridge WebSocket to the already-visible TUI session before
  `prompt.submit` / `session.steer`.
- `hermes_cli.main`: preserves the wrapper-provided
  `HERMES_TUI_ACTIVE_SESSION_FILE` so the bridge can discover the visible
  session written by the TUI.
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
plugin disabled, resident visible-session delivery and the Codex null-output
guard are not supplied by aify-comms.

The old in-place source edit path remains for emergency debugging only:

```bash
AIFY_HERMES_LEGACY_SOURCE_PATCH=1 bash install.sh --client hermes http://192.168.100.10:8800
```
