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
- Cross-repo: comms' `test_the_env_plugin_addresses_routes_this_service_serves.py` pattern, for the new local route.

## Revert

The launcher keeps the old route behind the capability check, so stopping the advertisement sends every next launch
back to `hermes-managed-host.js ensure-host`. Nothing in aify-comms is deleted until H4.
