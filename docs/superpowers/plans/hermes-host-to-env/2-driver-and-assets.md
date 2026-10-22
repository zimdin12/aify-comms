# 2. H2: the hermes driver, the python plugin and the install steps move to aify-wrapper

## Step one: split the mixed files by symbol

The harness-knowledge doc asks for the classification at the symbol and import-edge level before anything moves. The
four classes are PURE_DRIVER, SERVICE_ADAPTER, PROCESS_OWNER and COMPOSITION, and anything left unclassified fails.

So the first commit is a test in aify-comms that:
- reads every hermes-named module under `mcp/stdio`;
- maps each exported symbol to a class from a table in the test;
- fails on any export the table does not name.

It goes red the day someone adds an export without deciding where it lives. The table is the move list for H1, H2
and H3.

## What moves to aify-wrapper, as a library

These are the PURE_DRIVER symbols:
- gateway JSON-RPC and ACP framing: `hermes-gateway-protocol.js`, `hermes-acp-protocol.js`;
- session-id derivation, and finding and keeping the agent's session: `hermes-active-session.mjs`;
- effort per session: `hermes-session-effort.mjs`;
- the gateway token scrape from the index page.

The rule from the harness-knowledge doc applies: the library is instance-scoped over injected handles. It holds no
module-level session pool, no loop, no heartbeat, no HTTP to any service and no timers of its own.

## What moves to aify-wrapper, as assets

- **The python shim** in `integrations/hermes-aify-plugin`, unchanged.
- **The plugin loader** that `install_hermes_plugin` writes into `<hermes home>/plugins/aify-comms/`. It is renamed
  for aify-wrapper. The old directory is removed only after the new one loads (page 6).
- **`prebuild_hermes_web_dist`**, the prebuilt TUI bundle.
- **`patch_hermes_codex_stream_none_fallback`.** It edits hermes' own install tree, so moving it is also a chance to
  ask whether hermes upstream has fixed the bug it patches. The step is dropped if it has.
- **`install_hermes_turn_hooks` and the parts of `install_hermes_config` that are hermes settings.** The one line that
  names the comms MCP server stays the service's: it is the service's tool registration.

These run from aify-wrapper's install, which the guided-install chain already calls (S2). aify-comms' `install.sh
--client hermes` calls the chain and stops doing them itself.

## Tests that fail when it breaks

- The symbol table test, red on an unclassified export.
- aify-wrapper: the plugin loads from aify-wrapper's install with no aify-comms checkout on the machine. Run in a
  container with a pinned hermes, the S2 witness pattern.
- aify-wrapper: a second, renamed plugin beside the old one does not register the gateway methods twice.
- Size and mirror gates: both repos' 1000-line gates; `install.sh` shrinks, so its ceiling is lowered in the same
  commit.

## Revert

The launcher points `AIFY_HERMES_PLUGIN_PATH` at whichever copy its render names. Re-rendering with the old path
undoes the move for the next launch. The aify-comms copy is not deleted until H4.
