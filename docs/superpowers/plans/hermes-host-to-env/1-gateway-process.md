# 1. H1: aify-env owns the gateway process

## What moves

aify-env starts, proves, stops and reaps each hermes agent's hidden `hermes dashboard` gateway, the way it already
owns every PTY. From aify-comms, by symbol:
- `ensureGatewayHost`, `teardownGatewayHost`, `maybeReEnsureGatewayHost` and its no-recovery budget
  (`nextReEnsureBudget`);
- `stopDaemon` and `reapPriorHermes`;
- the port and the markers: `agentPort`, `resolveGatewayPort`, `writeGatewayUrlMarker`, `clearGatewayMarkers`.

aify-env gains a gateway owner in its host tier, generic over "a helper process an agent's launcher asks for". The
token scrape from the gateway's index page, and which command line counts as hermes, come from aify-wrapper's hermes
driver (page 2). So the host tier still learns no harness: it runs what the driver tells it to run.

## The seam

- The launcher asks aify-env for the gateway. Today it runs `node $AIFY_HERMES_STDIO_DIR/hermes-managed-host.js
  ensure-host`.
- aify-env answers with `{port, wsUrl, tokenRef}` on its local API. The token travels by reference, never in argv.
- aify-env advertises a capability, `agent-helper/hermes-gateway`. The launcher takes the new route only when it is
  advertised (page 6).
- **The advertisement is resolved on each ask, from aify-env's `~/.aify/config.json`.** It is never loaded once at
  startup. That is what lets a withdrawal reach the next launch without an aify-env restart. aify-env already resolves
  its service advertisement per ask (`currentAdvertisementBody`, "a registry edit must reach a running plugin
  without a restart"), and this follows the same shape.
- **That shape is not yet this behaviour** (revision 3). `currentAdvertisementBody` recomputes facts per ask, but
  nothing rereads a config flag per ask today, and the proposed helper flags do not exist. H1 builds the per-ask
  reread, and its history test below is what proves it.
- A gateway aify-env started is one aify-env stops: the agent's stop, kill and remove close it in the same lifecycle
  step as the TUI.

## What stays where it is in this slice

The delivery loop still runs from aify-comms' copy, and it reads the gateway URL marker exactly as now. H1 changes
who starts the process, not who talks to it.

## Tests that fail when it breaks

- aify-env: an agent's stop leaves no gateway process. Use a fake gateway executable in a sealed home, and watch it
  fail with the teardown removed.
- aify-env: two launches of one agent leave one gateway. This is today's prior-reap property, now owned by aify-env.
- aify-env: a gateway that binds and then dies is re-started at most three times without a recovery. This is the
  existing budget, carried over with its test.
- aify-wrapper: the launcher takes the aify-env route only when the capability is advertised, and the old route
  otherwise. A rendered launcher is tested with both answers.
- aify-env, the rollback history (revision 2):
  1. a daemon starts with the setting true;
  2. the setting is changed to false in its config file, with no restart;
  3. the next ask answers "not advertised".
  The test is watched failing with the read moved to startup.
- Cross-repo: comms' `test_the_env_plugin_addresses_routes_this_service_serves.py` pattern, for the new local route.

## Revert

The launcher keeps the old route behind the capability check. Setting the config value to false sends every launch
that asks after the edit back to `hermes-managed-host.js ensure-host`, with no aify-env restart (page 6 names the
event). Nothing in aify-comms is deleted until H4.
