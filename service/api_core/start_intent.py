"""What a start asks a host to do about an instance of the agent that is ALREADY running.

aify-wrapper's launchers hold a per-agent lease on the host (its agent-lease library). A launch carrying
REPLACE stops a live instance of its agent before it starts; one carrying START is refused by it. What
a dead instance left behind is stopped either way.

THE OPERATOR'S RULE, 2026-09-14: a live instance is replaced only on an EXPLICIT start -- a person at the
dashboard, a restart or recreate (the dashboard's, or an agent's `comms_restart`). Anything automatic --
a message cold-starting a lane, the queued-run backstop, an agent's `comms_spawn` -- is refused by a live
instance. Replacing on every start was built once and reverted: a message woke an idle lane and the
host killed four working sessions in ten minutes (aify-env `terminal-controls.mjs`).

WHY A FIELD. `spawn_requests.created_by` cannot say it: a cold start records the SENDER's agent id, so a
message waking a lane reads exactly like that agent spawning it on purpose.

WHERE IT TRAVELS. Decided where the start is asked for, stored on the spawn request, stamped on the ONE
terminal that request brings up, and handed to that terminal's launch as `AIFY_START_INTENT`. Every
other terminal -- a PTY recovered for a dispatch, anything relaunched later -- carries START, so an old
intent can never replace a live instance on a start nobody asked for.
"""

from typing import Any

START = "start"
REPLACE = "replace"
START_INTENTS = (START, REPLACE)


def normalize_start_intent(value: Any) -> str:
    """A stored or supplied intent, with anything unrecognised read as the safe one."""
    text = str(value if value is not None else "").strip().lower()
    return text if text in START_INTENTS else START


def start_intent_for_requester(requested_by: Any) -> str:
    """REPLACE only for a request that says it is the dashboard's; START for anyone else.

    AN ABSENT REQUESTER IS NOT THE DASHBOARD here, although the attribution columns record it as one.
    Both dashboard writers send `dashboard` explicitly, while an agent's `comms_spawn` accepts an empty
    `from` and any HTTP caller can omit the field -- and reading those as the dashboard would let them
    end a live instance. Guessing wrong in this direction costs a refused start, which is retried;
    guessing wrong in the other costs somebody's working session.
    """
    return REPLACE if str(requested_by or "").strip() == "dashboard" else START


def start_intent_for_spawn(requested_by: Any, agent_id: Any, metadata: Any) -> str:
    """The intent of a spawn request: the requester's, except that a HANDOFF of an agent to itself replaces.

    `comms_compact` into the same agent id asks, explicitly, for that agent's live worker to give way to
    a fresh-context one. Stored as START, the live worker it names would refuse its own successor -- the
    dashboard's identical handoff already replaces, because the dashboard is its requester.
    """
    meta = metadata if isinstance(metadata, dict) else {}
    target = str(agent_id or "").strip()
    if meta.get("compactMode") == "handoff" and target and str(meta.get("compactedFromAgentId") or "").strip() == target:
        return REPLACE
    return start_intent_for_requester(requested_by)
