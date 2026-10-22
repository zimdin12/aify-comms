# 4. H4: what aify-comms deletes, and what keeps it deleted

H4 runs only after the watch period on page 6 ends with no rollback. It is the one slice that is not revertible by a
capability switch, so it is a single commit that can be reverted as a commit.

## Deleted from aify-comms

- Every `mcp/stdio` module the H2 symbol table classes as PROCESS_OWNER or PURE_DRIVER for hermes, and the delivery
  composition. aify-wrapper and aify-env now hold them.
- `integrations/hermes-aify-plugin`.
- From `install.sh`, the hermes host steps: `copy_hermes_assets`, `prebuild_hermes_web_dist`,
  `patch_hermes_codex_stream_none_fallback`, `install_hermes_plugin`, `install_hermes_turn_hooks` and their callers.
  The `install.sh` ceiling gate is lowered to the new size in the same commit.
- The `gateway-orphans` row of `aify-comms doctor`, and the table row in CLAUDE.md. It lives in `aify-env doctor`.
- The launcher's old route, in aify-wrapper, in the same release.

## Kept in aify-comms

- **The service's hermes knowledge.** `service/runtimes/hermes.py`, delivery routing and the run states are the
  service's own records of what a hermes agent is.
- **The in-session MCP tools.** The `comms_*` tools are the service's interface. Which transport serves them on a
  host (the stdio bridge copy or SSE) is TARGET_ARCHITECTURE's open gap 1. It is not this plan's.

## Gates that keep it deleted

- `service/tests/test_service_runtime_boundary.py` gains a rule: no file under `mcp/stdio` spawns a hermes executable.
  The population is every spawn call in the bridge, derived and not listed, with a positive control that finds a
  spawn the scan must see.
- The H2 symbol table test, reduced to what remains, still fails on an unclassified export.
- `mcp/stdio/tests/doctor-sources.mjs` and `service/tests/doctor_sources.py` derive the doctor's files, so the
  removed check file is caught if anything still imports it.

## Before deleting

Each deleted symbol is shown to have no remaining caller, using the reference resolver rather than grep, positive
controlled on a symbol known to have callers. A live hermes agent still on the old route is the one caller a source
search cannot see, so page 6's reading of every running agent's route goes with the commit.
