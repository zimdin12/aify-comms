# 6. How the live hermes agents cross over, and how each slice rolls back

## The constraints

- **Restarts and relaunches are the operator's, and nobody asks him for one** (DECISIONS, 2026-10-22: no forced
  aify-env restart; the switch waits for his next ordinary one). A slice is live for an agent only from that agent's
  next launch after the code is installed.
- **One go-live** ("build all and we switch all together", 2026-10-19). H1-H3 go live in the same window as the rest
  of 0.9.8 if they are qualified by then. If his next ordinary restart comes first, they wait for the one after it.
  Nothing is pushed to make a date.
- **Messages to an agent that cannot be woken are kept** (the 0.9.8 rule). A broken route therefore loses no message:
  it holds work until the agent runs again.

## The route is chosen per launch

The launcher takes the new route when two things are true:
- aify-env advertises `agent-helper/hermes-gateway` (H1) or `agent-helper/hermes-delivery` (H3);
- the rendered launcher holds the new route, from aify-wrapper's install.

Otherwise it takes today's route, unchanged. The advertisement is a configuration value of aify-env, never a
constant. It is resolved on each ask, never at startup (revision 2; H1 builds and tests that read).

So the fleet may be mixed for a time: one agent on the old route, the next on the new. That is safe if two
conditions hold.
- **Each agent has exactly one owner of its gateway.** The new route's prior-reap recognises both process shapes
  before it starts a gateway. This has a test in H1.
- **aify-env's `managed-orphans-check` recognises both shapes.** This has a test in H3.

## What is read at go-live, raw rows kept

- **For every running hermes agent, the route it is on:** the command lines of its gateway and its delivery helper,
  read from the process table, plus the launcher render each was started from.
- **The installed aify-wrapper and aify-env commits,** compared with the qualified ones the same way SW6 maps the
  loaded aify-env build (page 3 of the S4 plan).
- **The S4 watch list, extended with hermes rows:**
  - a delivered message is processed in a later turn, never inside an active one;
  - a 4009 requeues only at a turn boundary;
  - stopping an agent leaves neither helper running.

## Rollback

- **H1 and H3:**
  - **Withdrawal** is the value set to false in `~/.aify/config.json`. It is a rollback action under the go-live's
    rollback authority, the same standing as SW6's revert.
  - **Activation** is the next launch's ask to aify-env. That launch takes the old route, with no aify-env restart.
  - **If H1 does not land a per-ask read** (its rollback test is not green), this activation event does not exist.
    The rollback then waits for the operator's next ordinary aify-env restart, and this page is amended to say so
    before go-live. No faster recovery is claimed, and no restart is forced.
- **An agent already running on the new route** keeps it until its next launch. If its delivery is broken, its
  messages are kept, and the operator's ordinary relaunch of that agent puts it on the old route. This is the cost of
  not forcing relaunches, and it is stated rather than hidden.
- **H2** rolls back by re-rendering the launcher with the old plugin path.
- **H4 waits for a clean watch period:** one working day of normal fleet use with every running hermes agent on the
  new route, ended by the operator, the same default as SW6.
