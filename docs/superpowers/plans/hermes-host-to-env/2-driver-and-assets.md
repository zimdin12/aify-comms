# 2. H2: the hermes driver, the python plugin and the install steps move to aify-wrapper

## Step one: split the mixed files by symbol

The harness-knowledge doc asks for the classification at the symbol and import-edge level before anything moves. The
four classes are PURE_DRIVER, SERVICE_ADAPTER, PROCESS_OWNER and COMPOSITION, and anything left unclassified fails.

So the first commit is a census test in aify-comms (revision 2: an exported-name table alone stays green when a
private closure or an import edge changes owner, so it is not enough).

**The population.** It is rooted at the entry points the host actually runs, not at filenames:
- `hermes-managed-host.js` `runCli` (`ensure-host`, `run`, `resolve-session`);
- `hermes-daemon-cli.js`;
- the `hermes-endpoint.js` functions the launcher imports with `node -e`;
- `server.js`'s hermes paths (`adapters/hermes.js`).

From those roots, every module reached by static import edges is in, whatever its name.

**The admitted grammar.** It is explicit and parsed with the repository's own parser, never a hand-rolled one.

| node | counted as |
|---|---|
| static `import` and `import()` of a string literal | an edge |
| `import()` of anything else | UNKNOWN, and fails |
| `child_process` spawn, exec and fork calls | a process effect |
| calls through `aify-http` or `fetch` | a service-HTTP effect |
| `setInterval` and `setTimeout` | a timer |
| heartbeat imports | a heartbeat |
| WebSocket opens | a gateway connection |
| writes under TEMP | a marker |
| `process.on` handlers | a signal effect |
| top-level `let` and mutable `Map` or `Set` | shared state |

Each occurrence counts, including those inside private closures. One example is `spawnJoiningLease`, the closure
inside `runEnsureHostCli` that attaches a new child to the lease.

**The table.** Every occurrence maps to a class (PURE_DRIVER, SERVICE_ADAPTER, PROCESS_OWNER, COMPOSITION) and an
owner, and an unmapped one fails.

**The controls:**
- **Positive:** the census finds `spawnJoiningLease` and at least one service-HTTP call in `hermes-delivery-loop.mjs`.
- **Mutants, with the export list unchanged,** each of which must fail the test:
  - a new private spawn in a non-exported closure;
  - a new `aify-http` call;
  - a new top-level `Map`.

The table is the move list for H1, H2 and H3.

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
