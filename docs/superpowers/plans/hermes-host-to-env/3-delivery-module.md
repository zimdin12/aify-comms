# 3. H3: delivery becomes aify-wrapper's hermes comms module, run by aify-env

## Where it lands, and why there

The delivery loop (`runDeliveryLoop` and `hermes-delivery-run.mjs`) is a COMPOSITION: it claims runs from aify-comms,
puts the message into the hermes session, and reports the run. The operator's roles statement places it: aify-wrapper's
comms module, switched on when the comms service exists. The claude channel plugin is the same pattern
(aify-wrapper 098/channel-plugin).

Two layers, kept apart:
- **The driver library (page 2)** is pure and instance-scoped.
- **The comms module** is the service adapter. It owns the claim, the cadence and the reporting, against aify-comms'
  existing HTTP routes. Nothing in the module is hermes framing, and nothing in the driver knows aify-comms.

The process is aify-env's. aify-env starts the module beside the gateway, as the second agent helper, and stops it
with the agent. Today the launcher starts it with `nohup node ... hermes-managed-host.js run <agent>`, which nothing
supervises.

## What changes in behaviour

- **Nothing about delivery.** It still uses `prompt.submit` when idle, `session.steer` when a steerable run is busy,
  and a 4009 queue that waits for the turn boundary. The same tests move with the code: the hermes steer/interrupt
  rule in DECISIONS (2026-07-20) is the one most worth keeping red-capable.
- **Status no longer comes from here.** After S4, aify-env decides busy and aify-comms never times turns (DECISIONS,
  2026-10-20). The gateway turn detector's posts to `/turn-start` and `/turn-end` retire. Only the delivery credit
  stays (`dispatchTurnOpen`, which a successful delivery sets and a turn end revokes), because it is a delivery
  decision.
- **aify-env's `managed-orphans-check` matches the new helper's command line** as well as the old one, for the whole
  mixed period (page 6).

## Tests that fail when it breaks

- The moved delivery tests, run against the module with a fake gateway:
  - a busy steerable run steers and never submits;
  - a 4009 requeues and drains on the next pass;
  - a turn that was already running is never delivered into.
- The S4 sandbox gate (`scripts/sandbox/gate`) gains a hermes case on the new route. It needs a hermes in the image,
  or a recorded gateway stub that fails the way the real one does. Which of the two is decided when the case is
  built, after measuring whether a pinned hermes runs in the sandbox image.
- aify-env: stopping the agent leaves neither helper running.

## Revert

The same capability check as H1, resolved per ask in the same way. With the value withdrawn, a launch that asks after
the edit starts the old `hermes-managed-host.js run` loop from aify-comms' copy, which H4 has not yet deleted.
