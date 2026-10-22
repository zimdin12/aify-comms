# 4. H4: what aify-comms deletes, and what keeps it deleted

H4 runs only after the watch period on page 6 ends with no rollback. It is the one slice that is not revertible by a
capability switch, so it is a single commit that can be reverted as a commit.

## The population H4 must close (revision 2)

Watching the running agents is not enough: deleting the old route breaks every future launch that would still take
it. Each class below is either closed by evidence or reported UNKNOWN, and H4 does not delete while any class is
UNKNOWN.

| class | how it is closed |
|---|---|
| running hermes agents | page 6's per-agent route reading, raw rows kept |
| every hermes definition, active or not (13 on this host on 2026-10-22) | each one launched once in the sandbox against the deletion candidate, from its own definition |
| installed launcher renders on this host | the render records aify-wrapper keeps; each one is re-rendered by the H4 install, or refuses (below) |
| aify-comms' bridge copy (`~/.aify-comms/mcp/stdio`) | the H4 install removes the deleted host modules from the copy, so an old render cannot half-start against stale files |
| the hermes plugin directory's loader | the H4 install removes the old loader once the new one is present |
| other hosts registered with the service (WSL, any remote) | UNKNOWN unless that host's own aify-env answers with its loaded version and capability |

**The minimum gate.** The H4 release raises comms' `MINIMUM_AIFY_ENV_VERSION` (`tier-version-check.mjs`) to the
first aify-env that advertises both helper capabilities. A launch on a host below it, or with the capability absent,
does not fall back to deleted code. It refuses, non-destructively: no process started, nothing written, and an exit
that names the needed aify-env version and the update command.

**The controls, against the deletion candidate:**
- **Adverse, a dormant old consumer:** an old render, launched after the H4 install, refuses with the guidance above
  and starts nothing.
- **Adverse, a capability-absent host:** an aify-env that does not advertise the capability gets the same refusal.
- **Healthy, an upgraded host:** a current render and aify-env deliver a message end to end on the new route.

## Deleted from aify-comms

- Every `mcp/stdio` module the H2 census classes as PROCESS_OWNER or PURE_DRIVER for hermes, and the delivery
  composition. aify-wrapper and aify-env now hold them.
- `integrations/hermes-aify-plugin`.
- From `install.sh`, the hermes host steps: `copy_hermes_assets`, `prebuild_hermes_web_dist`,
  `patch_hermes_codex_stream_none_fallback`, `install_hermes_plugin`, `install_hermes_turn_hooks` and their callers.
  The `install.sh` ceiling gate is lowered to the new size in the same commit.
- The `gateway-orphans` row of `aify-comms doctor`, and the table row in CLAUDE.md. It lives in `aify-env doctor`.
- The launcher's old route, in aify-wrapper, in the same release. It is replaced by the refusal above.

## Kept in aify-comms

- **The service's hermes knowledge.** `service/runtimes/hermes.py`, delivery routing and the run states are the
  service's own records of what a hermes agent is.
- **The in-session MCP bridge.** `server.js` and the `comms_*` tools remain on the host until TARGET_ARCHITECTURE's
  gap 1 closes (README).

## Gates that keep it deleted

- `service/tests/test_service_runtime_boundary.py` gains a rule: no file under `mcp/stdio` spawns a hermes executable.
  The population is every spawn call in the bridge, derived and not listed, with a positive control that finds a
  spawn the scan must see.
- The H2 census, reduced to what remains, still fails on an unclassified occurrence.
- `mcp/stdio/tests/doctor-sources.mjs` and `service/tests/doctor_sources.py` derive the doctor's files, so the
  removed check file is caught if anything still imports it.

## Callers are a census, not a search

**The reference resolver supplies real callers, but it does not certify that there are none.** The census for each
deleted symbol and file covers:
- the source graph (resolver plus the H2 census);
- the launcher templates, which hold literal paths into the bridge copy, such as `hermes-endpoint.js` in a `node -e`
  import and `hermes-daemon-cli.js`;
- the plugin loader's baked path;
- the installed copies and renders on this host;
- the saved definitions.

A class with no evidence either way is UNKNOWN and blocks the delete. It is not counted as absent.
