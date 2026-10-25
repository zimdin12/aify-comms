"""The agent status vocabulary: the words, which ones are live, which mean a worker at rest.

Until 0.9.9 S3b this module also held the legacy derivation (`derive()`) and its turn-event state machine. Since the
0.9.8 switch every word comes from the operational view over aify-env's records, so only the vocabulary is left; the
view, the dashboard twins and the readiness gates read it. S3c moves it to a module named for what it is.
"""
from __future__ import annotations

VALID_STATUSES = (
    "working", "shell", "online", "available", "blocked", "offline", "stopped", "misconfigured", "starting",
)

#: The statuses that mean this agent is NOT live. Everything else is.
#:
#: IT HAD NO PYTHON OWNER, and two places disagreed about it. `service/new_dashboard/status.js`
#: declares `NON_LIVE_AGENT_STATUSES` with these three; the analytics board counted the live
#: fleet inline as `not status.startswith("offline") and not status.startswith("stopped")`,
#: which counts a MISCONFIGURED agent as live. The contract's own meaning for that status is
#: "Identity exists but can never start. Not send-recoverable; a human must fix the config."
#:
#: The consequence was not only a wrong headline number. `onlineAgents` is the DENOMINATOR of
#: fleet utilization (`fleet_working / (online_count * window)`), so an agent that can never
#: start was diluting the percentage that says how hard the fleet is working.
#:
#: Declared here rather than in the dashboard because that is the shape this repo already used
#: for `env_status.ENVIRONMENT_STATUSES`: the JS set was the only complete statement of the
#: vocabulary, declaring the Python owner made it bindable, and the twins gate demanded the
#: binding on the same run.
NON_LIVE_AGENT_STATUSES = ("offline", "stopped", "misconfigured")

#: Statuses that claim a managed worker is running and IDLE at its prompt, which is exactly what the
#: roster's live-worker gate checks against a live terminal. It listed `online` and `ready` until
#: 0.7.0: `ready` is not a status, and `shell` -- the other "worker at its prompt" -- was missing, so a
#: cached `shell` outlived its terminal (v0.7 scan A8).
WORKER_AT_REST_STATUSES = ("online", "shell")


def is_live_agent_status(status) -> bool:
    """Whether an agent with this status counts toward the live fleet.

    Prefix-tolerant, because the analytics board matched with `startswith` -- a derived status
    can carry a suffix (`offline (no wake path)`), and an exact-equality port would have
    silently started counting those as live.
    """
    value = str(status or "").strip().lower()
    if not value:
        # Guards fail closed: an agent whose status could not be derived is not evidence of a
        # live worker, and counting it would inflate the utilization denominator.
        return False
    return not any(value.startswith(dead) for dead in NON_LIVE_AGENT_STATUSES)
