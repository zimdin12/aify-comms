"""Pure, event-driven status state machine (status v2 → proof-based v3, 2026-06-18).

The ONE place agent status is decided. `derive()` is a pure function of explicit
inputs (no DB, no clock) so it is exhaustively table-testable and encodes the
status matrix as ordered rules instead of a sprawling per-request derivation.

PROOF-BASED model (2026-06-18): status is what the wrapper PROVES (turn events +
liveness heartbeat) plus the states aify-comms itself owns (available / offline /
stopped). No time-decay. Vocabulary is now 6 states — `idle` and `stale` were
time-decay artifacts and are removed: an alive-not-in-turn agent is `online`
(never `idle`), and a resident whose bridge heartbeat is gone is `offline`
(never `stale`). Inputs are gathered in api_v2._gather_status_inputs from events
+ a single short liveness window (no idle/offline MINUTE thresholds).
"""
from __future__ import annotations
from dataclasses import dataclass

VALID_STATUSES = (
    "working", "online", "available", "blocked", "offline", "stopped",
)


@dataclass(frozen=True)
class StatusInputs:
    mode: str                 # "managed" | "resident"
    alive: bool               # heartbeat within the liveness window
    in_turn: bool             # turn_start seen, no turn_end yet
    awaiting_input: bool      # console looks like it needs input
    worker_present: bool      # managed: live console+sidecar / gateway / wrapper-child
    env_reachable: bool       # managed: owning environment bridge online
    disabled: bool            # explicit stop/disable
    bridge_stale: bool        # resident: bridge heartbeat missing (→ offline)
    has_live_session: bool    # resident: a live runtime session exists
    idle_too_long: bool = False   # DEPRECATED (unused) — removed with the byproduct builder in Phase 3
    console_booting: bool = False  # managed: console up, sidecar not yet claimed (display online)


def derive(i: StatusInputs) -> str:
    """Map explicit inputs to one of VALID_STATUSES. First match wins."""
    # Explicit stop / unreachable managed environment wins FIRST, regardless of turn state:
    # a stopped or env-down agent is stopped/offline even mid-"turn" (the turn can't run).
    if i.disabled:
        return "stopped"
    if i.mode == "managed" and not i.env_reachable:
        return "offline"
    # AFTER those short-circuits, a turn in flight dominates the remaining LIVE states (so a
    # long turn never falls back to online/available — NOT offline, which already won above).
    # Liveness gates the in-turn states: a turn signal must not outlive the worker. A dead
    # managed worker / stale resident bridge with a stale in_turn=1 falls through to
    # available/offline. Live turns are unaffected — every real in_turn→working path has
    # worker_present (managed) / has_live_session (resident) true.
    live = i.worker_present if i.mode == "managed" else (i.has_live_session and not i.bridge_stale)
    if i.in_turn and live:
        return "blocked" if i.awaiting_input else "working"
    if i.mode == "managed":
        if i.alive and i.worker_present:
            return "online"
        if i.env_reachable:
            # A console that is up but whose sidecar hasn't claimed yet is BOOTING →
            # display `online` so the operator doesn't miss the live terminal. Routing is
            # unaffected: delivery keys on worker_present/has_live_worker, which stays False
            # until the sidecar claims, so a send during boot still queues.
            return "online" if i.console_booting else "available"
        return "offline"
    # resident: alive with a live session + fresh bridge → online; otherwise gone → offline
    # (the heartbeat going silent is the proof it's gone; there is no separate 'stale' decay).
    if i.alive and i.has_live_session and not i.bridge_stale:
        return "online"
    return "offline"


EVENT_KINDS = ("turn_start", "turn_end", "blocked", "unblocked")

def apply_event(state: dict, event: dict) -> dict:
    """Fold an event into the per-agent turn sub-state (dict copy returned).
    Liveness / worker_present / env_reachable are NOT stored here — they are
    gathered live (heartbeat lease, bridge rows) at derive() time. This only
    tracks turn flags driven by push events.
    """
    s = dict(state)
    kind = str(event.get("kind") or "")
    if kind == "turn_start":
        s["in_turn"] = 1
        s["turn_run_id"] = str(event.get("runId") or "")
        s["awaiting_input"] = 0
    elif kind == "turn_end":
        s["in_turn"] = 0
        s["turn_run_id"] = ""
        s["awaiting_input"] = 0
    elif kind == "blocked":
        s["awaiting_input"] = 1
    elif kind == "unblocked":
        s["awaiting_input"] = 0
    return s
