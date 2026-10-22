"""What a send tells its sender about recipients it stored but could not wake.

THE OPERATOR'S RULE (Steven, 2026-10-16, DECISIONS.md "0.9.8"): a message to an agent that cannot be woken
is kept in its inbox, and the answer says the wake failed and that the agent can read it when it is
online. Before this, a woken send to such an agent was refused and nothing was stored.

"CAN READ", NOT "WILL READ": nothing here starts a later delivery, and no sweep recovers a direct message,
so the notice promises only that the message is waiting.

Pure functions: hints in, text out.
"""
from __future__ import annotations

from typing import Any

REPLAY_NOTICE = (
    "This matched an earlier send of the same message, which was stored then. Whether that send woke its "
    "recipients is not known from this answer, and nothing was woken again."
)


def wake_failed_notice(not_started: list[dict[str, Any]]) -> str:
    """One sentence per recipient that was stored without a wake, each with its own reason."""
    parts = []
    for hint in not_started:
        who = str(hint.get("targetAgentId") or "").strip()
        if not who:
            continue
        reason = str(hint.get("reason") or "").strip() or "it cannot start live work now"
        if hint.get("externalSender"):
            # Its address is where IT says it lives, not a route: this service relays nowhere, so being
            # online elsewhere does not bring it this copy (dispatch_hint._unregistered_recipient_hint).
            parts.append(
                f"{who} is not registered here ({reason}). The message is stored on this service only and is "
                f"not relayed to {who}'s declared address."
            )
            continue
        parts.append(
            f"Wake failed for {who} ({reason}). The message is in {who}'s inbox; {who} can read it when it is online."
        )
    return " ".join(parts)


def held_notice(held: list[dict[str, Any]]) -> str:
    """One sentence per recipient whose run was queued with nothing that will start it, each with its own reason
    (SW5 review R3). The run is kept, so this says the message is waiting, never that the wake failed."""
    parts = []
    for hint in held:
        who = str(hint.get("targetAgentId") or "").strip()
        if who:
            parts.append(f"{who} was not woken and its message is held: {hint.get('reason')}.")
    return " ".join(parts)
