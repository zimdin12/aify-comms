# The hermes host moves out of aify-comms (task #4, plan revision 1, 2026-10-22)

The goal, from the dashboard task: no aify-comms code runs on an agent's host, hermes gateway hosting is aify-env's,
and a second aify-env (on WSL) serves a managed agent end to end. The remap tags this 0.9.12. The operator answered
Q5 on 2026-10-19: a WSL aify-env may run for the proof, and it is removed once 0.9 is done and deployed.

The owners follow the operator's statement of the repos' roles (DECISIONS, 2026-10-20):
- **aify-comms is a service.** It holds messages, their information and the database.
- **aify-env is the environment provider.** It owns every process, PTY, kill and reap on its host.
- **aify-wrapper wraps the harnesses.** Its comms module switches on when the service exists.

The driver rule from `docs/HARNESS_KNOWLEDGE_BELONGS_TO_AIFY_WRAPPER.md` still holds: hermes protocol
logic goes to aify-wrapper as a library, aify-env owns every physical process, and the library owns no daemon loop,
no session pool, no claim and no supervision.

## Pages

| page | what it settles |
|---|---|
| `0-inventory.md` | what aify-comms code runs on a hermes host today, measured, and whose it becomes |
| `1-gateway-process.md` | H1: aify-env starts, stops and reaps the per-agent gateway |
| `2-driver-and-assets.md` | H2: the hermes driver library, the python plugin and the install steps move to aify-wrapper |
| `3-delivery-module.md` | H3: the delivery loop becomes aify-wrapper's hermes comms module, run by aify-env |
| `4-deletions.md` | H4: what aify-comms deletes, and the gates that keep it deleted |
| `5-wsl-proof.md` | W: a throwaway WSL aify-env serves a managed agent end to end |
| `6-live-switch.md` | how the live hermes agents cross over, and how each slice rolls back |

## Order

H1, H2 and W start in parallel: W proves multiple environments and does not need hermes to move. H3 needs H1 and H2.
H4 runs only after every live hermes agent has run on the new route through a clean watch period (page 6).

Each slice is revertible on its own until H4: the old route stays installed and is chosen per launch (page 6).

## Prerequisites

- The status switch (S4, SW6) is live first. It retires the comms side of hermes turn detection, so H3 moves
  delivery, not status.
- The guided install (S2-S5) is merged, because H2 puts the hermes install steps into the chain it builds.

## Open questions for the operator

One, on page 5: which harness the WSL proof runs, and whether he does the one login it needs for a real model turn.
Nothing else here needs the operator. Every other choice follows the roles above, the one-go-live ruling and the
no-forced-restart ruling.
