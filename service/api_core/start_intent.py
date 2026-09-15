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
    """REPLACE for the dashboard, START for an agent. An absent requester is the dashboard, as every
    writer that defaults one already records it."""
    return REPLACE if str(requested_by or "").strip() in ("", "dashboard") else START
