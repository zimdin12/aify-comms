# 0.9.8: the hard switch to aify-env's word (2026-10-20, plan, not built)

This replaces S2-S4 and the C8 gate of `2026-10-18-status-switch.md`. It follows two operator rulings
(DECISIONS.md, 2026-10-20):
- **The gate is a sandbox, not a one-day live comparison.** Then a hard switch, with a rollback kept ready.
- **comms never times turns.** aify-env alone decides `busy`, and comms queues when aify-env is silent.

comparison-only and switch-needed items come from `2026-10-20-c8-drop-inventory.md`.

## The design in one paragraph

comms keeps what it owns: messages, runs and delivery. For "is this agent busy", it reads aify-env's published
`busy`, and nothing else. It keeps no delivery turn, no stale-turn estimate and no turn timer. An agent whose
aify-env is not reporting reads `unknown`, and messages to it wait in the queue. At start, comms reloads the
last records it applied, so a restart does not blank what it knew. The comparison machinery is deleted after
the rollback interval.

## Pages

- [What comms reads](s4-hard-switch/1-what-comms-reads.md): `holdsDelivery`, `unknown`, every reader that changes,
  why no delivery turn, the mirror reload.
- [The sandbox gate](s4-hard-switch/2-sandbox-gate.md): the stand-in agent, the cases, and each witness with what
  turns it red.
- [Switch, rollback, deletions](s4-hard-switch/3-switch-rollback-deletions.md): the S4 steps, the rollback, the watch
  list and its trigger, and the S5 order.

## Slices, in order, each shippable alone

| # | slice | repo | depends on |
|---|---|---|---|
| SW1 | Mirror reload at service start | comms | - |
| SW2 | `commsView` as product: a pure `holdsDelivery` and word, with no timers | comms | - |
| SW3 | The sandbox gate: a stand-in agent plus cases on the env-against-comms runner | comms (+ env read-only) | SW1, SW2 for the green run |
| SW4 | D8 rows 1 and 2; B4 confirmed or built | comms, env | - |
| SW5 | The switch: every reader rewired in one revertible commit; P7 witnesses updated per ruling | comms | SW1-SW4, SW3 green |
| SW6 | Deploy, watch, and roll back on the trigger | comms | SW5 |
| SW7 | S5 deletions, 0.9.9, after the interval | comms, wrapper | SW6 interval clean |

SW1, SW2 and SW4 run in parallel. SW3 is written against SW2's interface and goes green once SW1 and SW2 land.

## Decisions this plan asks for

1. **No run correlation (recommended).** The reminder skip and the stranded guard read "busy on any work" from
   aify-env, because aify-env's turn carries no run id. Two P7 witnesses change, as a ruled change. The
   alternative is for aify-env's turn to carry the reporter's `runId`: a schema change in env, comms and wrapper.
   See page 1.
2. **One clock stays in comms:** a publisher is fresh while comms applied its last publication under 180 s ago
   (`age_class`). That is comms timing its own receipt, on one machine, not timing a turn. Replacing it with a
   connection-based signal is a later option. See page 1.

## ASSUMED, not checked

- aify-env republishes often enough that a reporting host stays under 180 s. `SNAPSHOT_MS` = 60 s sets the
  snapshot epoch (env lib/agent-state-sender.mjs). The send trigger and cadence were not read.
- B4's implementation status. The host reads `given.screen` and `given.backgroundShells`, and the producers were
  not traced.
- A scripted stand-in agent can drive `POST /agents/:id/turn-event` (env lib/protocol.mjs) inside the sandbox
  without a real harness.
- After the switch, Hermes gateway turns reach aify-env through the wrapper's turn hooks.
