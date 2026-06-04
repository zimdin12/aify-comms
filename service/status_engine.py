"""Pure, event-driven status state machine (status v2, 2026-06-04).

The ONE place agent status is decided. `derive()` is a pure function of explicit
inputs (no DB, no clock) so it is exhaustively table-testable and encodes the
status matrix as ordered rules instead of a sprawling per-request derivation.
Status vocabulary is unchanged: working/online/idle/available/blocked/stale/
offline/stopped. Inputs are gathered elsewhere (api_v2._gather_status_inputs)
from events + a single liveness heartbeat.
"""
from __future__ import annotations
from dataclasses import dataclass

VALID_STATUSES = (
    "working", "online", "idle", "available", "blocked", "stale", "offline", "stopped",
)


@dataclass(frozen=True)
class StatusInputs:
    mode: str                 # "managed" | "resident"
    alive: bool               # heartbeat within liveness lease
    in_turn: bool             # turn_start seen, no turn_end yet
    awaiting_input: bool      # console looks like it needs input
    worker_present: bool      # managed: live console+sidecar / gateway / wrapper-child
    env_reachable: bool       # managed: owning environment bridge online
    disabled: bool            # explicit stop/disable
    bridge_stale: bool        # resident: bridge heartbeat missing
    has_live_session: bool    # resident: a live runtime session exists
    idle_too_long: bool       # online but quiet beyond idle window


def derive(i: StatusInputs) -> str:
    """Map explicit inputs to one of VALID_STATUSES. First match wins."""
    # Explicit stop / unreachable managed environment is offline regardless.
    if i.disabled:
        return "stopped"
    if i.mode == "managed" and not i.env_reachable:
        return "offline"
    # A turn in flight dominates (so a long turn never reads offline/available).
    if i.in_turn and i.awaiting_input:
        return "blocked"
    if i.in_turn:
        return "working"
    if i.mode == "managed":
        if i.alive and i.worker_present:
            return "idle" if i.idle_too_long else "online"
        if i.env_reachable:
            return "available"      # idle, no live worker, but lazy-autostartable
        return "offline"
    # resident
    if i.alive and i.has_live_session and not i.bridge_stale:
        return "idle" if i.idle_too_long else "online"
    if i.bridge_stale:
        return "stale"
    return "offline"
