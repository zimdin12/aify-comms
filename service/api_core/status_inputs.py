"""Build the StatusInputs the pure status engine derives from — the first LAYER-1 module.

Everything moved out of `service/control_plane.py` before this was layer 0: a helper whose whole
dependency closure already lived in one existing module, so it went there and no new module appeared.
`_gather_status_inputs` is the first one that cannot. It reads FOUR of those modules — `liveness`,
`managed_env`, `channel_delivery` and `status_engine` — and three of them already import `liveness`,
so joining any of them is a cycle in some direction. It sits above them all, and a module above a
layer needs to be its own module.

WHY THAT MATTERS BEYOND BOOKKEEPING. This is the join point where "is the bridge fresh", "is the
console booting", "is a worker present" and "is the environment reachable" become ONE snapshot handed
to `derive()`. Splitting those questions across the modules that answer them individually would put
the join back inside the control plane, which is where it has been all along.

`engine_status` comes with it because it IS the one-line composition of the two — gather, then derive
— and separating a function from its only caller when the caller is three lines long buys nothing.
`service/reconcilers/dispatch_lifecycle.py` reached it through a borrow shim that existed only because
importing the control plane from a reconciler is a cycle; importing this module is not, so the shim is
now a plain import.

NOTHING HERE DECIDES A STATUS. `derive()` in service/status_engine.py is the sole authority for that
and it is pure; this module's job is to gather the inputs honestly.
"""

from __future__ import annotations

from datetime import datetime, timezone

from service.api_core.capabilities import _managed_env_reachable
from service.api_core.channel_delivery import _has_live_worker_for
from service.api_core.liveness import (
    TURN_BUSY_BACKSTOP_SECONDS,
    _agent_awaiting_input,
    _agent_config_defect,
    _agent_wake_mode,
    _console_working_lease_fresh,
    _resident_bridge_is_fresh,
)
from service.api_core.managed_env import (
    _managed_console_is_booting,
    _managed_owning_environment_row,
    _managed_spawn_is_starting,
)
from service.api_core.runtime import _normalize_session_mode
from service.api_core.settings import _load_settings
from service.clock import iso_to_epoch as _iso_to_epoch
from service.status_engine import StatusInputs, derive


async def _gather_status_inputs(db, agent_row, *, settings=None) -> StatusInputs:
    """Build a StatusInputs from the SAME live signals the legacy derivation reads.

    No new derivation logic — just adapts existing signals (agent_status_state
    turn flags, _has_live_worker_for, _managed_owning_environment_row /
    _environment_effective_status for env reachability, _resident_bridge_is_fresh
    for resident liveness) into the engine's pure input contract. status v2.
    """
    settings = settings or await _load_settings(db)
    aid = agent_row["id"]
    mode = _normalize_session_mode(agent_row["session_mode"] or "resident")
    st = await (await db.execute(
        "SELECT in_turn, awaiting_input, last_event, last_event_at FROM agent_status_state WHERE agent_id=?", (aid,))).fetchone()
    in_turn = bool(st and st["in_turn"])
    awaiting_stored = bool(st and st["awaiting_input"])
    # status v2 (Fix B, 2026-06-05): in_turn staleness backstop. The OLD engine
    # clamps a stuck `working` via TURN_BUSY_BACKSTOP_SECONDS, but the NEW engine
    # had NO ceiling on in_turn — so an agent with a turn-START signal but a
    # DROPPED/absent turn-END (e.g. resident hermes, which has a start hook but no
    # end hook) would latch `working` forever. Treat in_turn as ended once the
    # row's last_event_at is older than the same backstop (dropped-event safety).
    if in_turn:
        last_event_epoch = _iso_to_epoch(st["last_event_at"] if st else "")
        if last_event_epoch and (
            datetime.now(timezone.utc).timestamp() - last_event_epoch
        ) > TURN_BUSY_BACKSTOP_SECONDS:
            in_turn = False
    # PURE-EVENT (2026-06-19): the turn-end GRACE was removed from BOTH status paths — this
    # WS-push path (_gather_status_inputs) and the byproduct/poll path (_compute_live_status_cache).
    # It held in_turn for 20s after a turn-END to mask a managed wrapper's premature Stop, but
    # that 20s time-decay is exactly what the operator rejects, and leaving it ONLY here made the
    # pushed status disagree with the polled status for 20s. The flap is fixed at the SOURCE (fast
    # bridge turn detectors re-assert a premature clear within a tick); derive() stays pure-event.
    # DISABLED = explicit stop OR wake disabled (launch_mode='none' — the operator's "Stop
    # wake"). The engine only knew 'stopped' (2026-06-12 audit): wake-disabled agents served
    # `available` under status_engine=new — inviting sends that can never wake them — while
    # the legacy path correctly said offline (Phase 3: offline = explicit disable). This was
    # the bulk of the old/new status-disagreement log noise (ef-* fleet).
    disabled = (
        str(agent_row["status"] or "").lower() == "stopped"
        or str(agent_row["launch_mode"] or "").lower() == "none"
    )
    # Compute the live-worker signal first (it gates the console-working lease below), then
    # fold the worker-gated spinner lease into in_turn — MUST match the byproduct path so the
    # WS-push status equals the served poll status (bughunt: lease was missing here).
    console_lease = await _console_working_lease_fresh(db, aid)
    if mode == "managed":
        env_row = await _managed_owning_environment_row(db, agent_row, resolved_environment_id="")
        env_reachable = _managed_env_reachable(agent_row, env_row, settings)
        worker_present = await _has_live_worker_for(db, agent_row, settings=settings)
        live_signal = worker_present
    else:
        worker_present = await _resident_bridge_is_fresh(db, agent_row,
            lease_seconds=int(settings.get("resident_lease_seconds", 150) or 150))
        live_signal = worker_present
    in_turn = in_turn or (console_lease and live_signal)
    # WS-5 (2026-06-17): make `blocked` reachable under new — an in-turn agent whose console
    # tail looks like it awaits operator input derives `blocked`. Gate on the (lease-folded)
    # in_turn so the terminal read is bounded to agents currently mid-turn.
    awaiting = awaiting_stored or (in_turn and await _agent_awaiting_input(db, aid))
    if mode == "managed":
        # WS-12 (2026-06-17): a managed console that is up but whose sidecar hasn't claimed yet
        # is BOOTING → display `online` (parity with the legacy display-only promotion). Only
        # relevant when it would otherwise read `available` (no worker, env reachable).
        console_booting = (
            not worker_present and env_reachable
            and await _managed_console_is_booting(db, aid)
        )
        config_defect = ""
        if not worker_present and env_reachable and not console_booting:
            config_defect = await _agent_config_defect(db, agent_row, mode)
        # The EARLIER boot phase than console_booting: a claimed spawn whose worker has not appeared
        # yet, bounded by SPAWN_STARTING_WINDOW_SECONDS so a spawn that never produces one stops
        # claiming to be on its way. Only computed when it could change the answer.
        spawn_starting = (
            not worker_present and env_reachable and not console_booting and not config_defect
            and await _managed_spawn_is_starting(db, aid)
        )
        return StatusInputs(mode=mode, alive=worker_present, in_turn=in_turn, awaiting_input=awaiting,
                            worker_present=worker_present, env_reachable=env_reachable, disabled=disabled,
                            bridge_stale=False, has_live_session=worker_present,
                            console_booting=console_booting, config_defect=config_defect,
                            spawn_starting=spawn_starting)
    # Phase I flip parity: a resident in a `*-missing-handle` wake-mode (no usable wake
    # handle — e.g. resident hermes with no live gatewayUrl, resident codex/pi without a
    # sessionHandle) CANNOT be woken, so it reads `stale` even if a bridge looks fresh
    # (mirrors the legacy resident missing-handle gate; matches the dashboard's red dot).
    missing_handle = str(_agent_wake_mode(agent_row) or "").endswith("-missing-handle")
    return StatusInputs(mode=mode, alive=worker_present, in_turn=in_turn, awaiting_input=awaiting,
                        worker_present=worker_present, env_reachable=True, disabled=disabled,
                        bridge_stale=(not worker_present) or missing_handle, has_live_session=worker_present,
                        console_booting=False,
                        config_defect=await _agent_config_defect(db, agent_row, mode, missing_handle=missing_handle))


async def engine_status(db, agent_row, *, settings=None) -> str:
    """status v2: serve one of VALID_STATUSES from the pure engine."""
    return derive(await _gather_status_inputs(db, agent_row, settings=settings))
