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
    "working", "online", "available", "blocked", "offline", "stopped", "misconfigured", "starting",
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
    console_booting: bool = False  # managed: console up, sidecar not yet claimed (display online)
    # managed: a spawn is RUNNING and its worker has not appeared yet — the earlier boot phase,
    # before there is any console for `console_booting` to see.
    #
    # THE BOUND IS PART OF THE INPUT, and that is the whole safety property. This flag must be
    # gathered as "starting AND within the startup window", never as "a spawn row says running".
    # A spawn that never produces a worker would otherwise read `starting` forever — which is what
    # happened to ef-manager on 2026-08-11, and rendering that as a hopeful transient would have
    # HIDDEN it instead of surfacing it. Past the window the agent falls back to exactly what it
    # reports today (`available`), so a broken spawn stays as visible as it was before this state
    # existed. Same rule as `unknown-all` in aify-doctor: a state that cannot expire is a false green.
    spawn_starting: bool = False
    # Structurally unable to start: the identity exists but something it NEEDS in order to ever
    # be triggered is missing (no spawn spec and no host that could cold-start it, no wake path,
    # an unknown runtime). This is a property of the CONFIG, not of the moment — unlike offline,
    # which says "not here right now", it says "sending to this will never work until a human
    # fixes it". Gathered in api_v2._gather_status_inputs; empty string means fine.
    config_defect: str = ""


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
            # `available` PROMISES cold-start on the next send. If the config makes that
            # impossible, saying `available` is a false promise that sends the operator hunting a
            # delivery bug — the same false-green class as a verifier that cannot fail. Report the
            # defect instead. Ranked here, below every live state, on purpose: an agent that is
            # demonstrably working is not misconfigured in any way that matters right now.
            if i.config_defect:
                return "misconfigured"
            if i.console_booting:
                return "online"
            # The EARLIER boot phase: a spawn is running and no console exists yet. Operator-
            # requested 2026-08-11, after watching an agent sit at `available` for 28 seconds
            # during a restart and reasonably reading it as "the restart failed" — because that
            # morning, an identical-looking `available` was exactly that.
            #
            # Ranked BELOW misconfigured and console_booting deliberately. An agent that can never
            # start is not starting, and one whose console is already up has better evidence than
            # a spawn row. Ranked ABOVE available because `available` promises a cold-start on the
            # next send, while this agent is already coming up — telling the operator "idle, send
            # something" during a boot invites a duplicate wake.
            #
            # It is a DISPLAY distinction only: delivery keys on worker_present, which stays false
            # here exactly as it did when this window read `available`, so routing is unchanged and
            # a send during boot still queues.
            if i.spawn_starting:
                return "starting"
            return "available"
        return "offline"
    # resident: alive with a live session + fresh bridge → online; otherwise gone → offline
    # (the heartbeat going silent is the proof it's gone; there is no separate 'stale' decay).
    if i.alive and i.has_live_session and not i.bridge_stale:
        return "online"
    # A resident with no usable wake path is not merely offline — it cannot be woken at all.
    if i.config_defect:
        return "misconfigured"
    return "offline"


EVENT_KINDS = ("turn_start", "turn_end", "blocked", "unblocked")

def _on_turn_start(s: dict, event: dict) -> None:
    s["in_turn"] = 1
    s["turn_run_id"] = str(event.get("runId") or "")
    s["awaiting_input"] = 0


def _on_turn_end(s: dict, event: dict) -> None:
    s["in_turn"] = 0
    s["turn_run_id"] = ""
    s["awaiting_input"] = 0


def _on_blocked(s: dict, event: dict) -> None:
    s["awaiting_input"] = 1


def _on_unblocked(s: dict, event: dict) -> None:
    s["awaiting_input"] = 0


#: The event vocabulary, as a table rather than an if-chain, so the KEYS are the vocabulary and
#: nothing has to restate it. "Derive allowed values, never list them" -- a second list of kinds kept
#: beside the handler is a defect with a delay on it, and this engine's whole job is agreeing with
#: itself about what an agent is doing.
_EVENT_HANDLERS = {
    "turn_start": _on_turn_start,
    "turn_end": _on_turn_end,
    "blocked": _on_blocked,
    "unblocked": _on_unblocked,
}

KNOWN_EVENT_KINDS = tuple(_EVENT_HANDLERS)


def is_known_event_kind(kind) -> bool:
    """Whether `apply_event` would do anything with this kind.

    Exists because ACCEPTING an unknown kind is correct and SILENTLY dropping it is not. The endpoint
    takes `kind: str` unconstrained and must keep doing so: a bridge is operator-launched and may run
    a NEWER version than the service, so a kind this service has never heard of has to be tolerated
    rather than rejected. What was missing is any way to tell the two apart -- an unrecognised kind
    returned `{"ok": true}`, wrote itself into `agent_status_state.last_event`, changed nothing, and
    said nothing. Debugging that means reading this function.
    """
    return str(kind or "") in _EVENT_HANDLERS


def apply_event(state: dict, event: dict) -> dict:
    """Fold an event into the per-agent turn sub-state (dict copy returned).
    Liveness / worker_present / env_reachable are NOT stored here — they are
    gathered live (heartbeat lease, bridge rows) at derive() time. This only
    tracks turn flags driven by push events.

    An unknown kind returns the state unchanged, deliberately -- see `is_known_event_kind` for why
    that is tolerance rather than negligence, and for what now reports it.
    """
    s = dict(state)
    handler = _EVENT_HANDLERS.get(str(event.get("kind") or ""))
    if handler is not None:
        handler(s, event)
    return s
