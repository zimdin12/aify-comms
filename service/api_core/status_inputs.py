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

from typing import Any, Optional

from service.api_core.active_run_lookup import (
    _current_active_run_row,
    _current_channel_awaiting_reply_run_row,
)
from service.api_core.agent_sessions import _current_agent_session_row
from service.api_core.capabilities import _managed_env_reachable, _row_capabilities
from service.api_core.channel_delivery import _has_live_worker_for, _worker_liveness_for
from service.api_core.turn_liveness_policy import turn_is_still_live
from service.api_core.claim_gating import (
    TURN_LEASE_ABSOLUTE_MAX_SECONDS, _turn_lease_is_renewable,
)
from service.api_core.liveness import (
    CONSOLE_WORKING_LEASE_SECONDS,
    TURN_BUSY_BACKSTOP_SECONDS,
    _LIVE_SESSION_STATUSES,
    _agent_awaiting_input,
    _agent_config_defect,
    _agent_wake_mode,
    _console_working_lease_fresh,
)
from service.api_core.live_process_probes import _resident_bridge_is_fresh
from service.api_core.managed_env import (
    ConsoleBootingOnce,
    _managed_console_is_booting,
    _managed_owning_environment_row,
    _managed_spawn_is_starting,
)
from service.api_core.manual_status import _MANUAL_STATUSES
from service.api_core.records import _row_status_note
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.serialization import _iso_add_seconds, _json_loads_or
from service.api_core.settings import _load_settings
from service.api_core.status_decision import StatusFacts, _decide_effective_status
from service.api_core.turn_state import _status_turn_signals
from service.api_core.terminal_status import _TERMINAL_ACTIVE_STATUSES
from service.api_core.terminal_text import (
    _terminal_prompt_hint_from_raw,
    _terminal_prompt_hint_from_screen,
)
from service.terminal_snapshot import render_live_screen
from service.clock import iso_to_epoch as _iso_to_epoch, now as _now
from service.api_core.status_signal_prefetch import status_signals_or_live
from service.env_status import environment_effective_status as _environment_effective_status
from service.reconcilers.status_cache import _status_refresh_after
from service.status_engine import StatusInputs, derive


def _turn_anchor(state_row) -> str:
    """When the CURRENT turn began, for a ceiling that must not be postponable.

    `turn_started_at` is stamped on the not-busy -> busy transition and then left alone, so a poster
    that re-stamps on a timer cannot move it. `last_event_at` moves on every event and is what the
    two in_turn clamps used to read -- which is why the hermes hook, firing `turn_start` before every
    model call, kept a latched agent reading `working` for ever.

    THE FALLBACK IS FOR OLD ROWS ONLY. A row written before the column existed has an empty anchor,
    and returning nothing there would DISABLE the ceiling for exactly those rows. The boot backfill
    fills them in, so this covers the window between a row being read and that backfill running.

    ONE READER, because two clamps asking one question from different columns is how a documented
    parity quietly stops holding -- which is the bug directly above this one in the same file.
    """
    if not state_row:
        return ""
    anchor = ""
    try:
        anchor = str(state_row["turn_started_at"] or "")
    except (KeyError, IndexError):
        # A row selected without the column (an older query, or a test fixture) is not evidence that
        # the turn just started. Fall through to the pre-anchor behaviour rather than inventing one.
        anchor = ""
    if anchor:
        return anchor
    try:
        return str(state_row["last_event_at"] or "")
    except (KeyError, IndexError):
        return ""


def _in_turn_survives_the_ceiling(state_row, *, renewable: bool = False) -> bool:
    """The in_turn clamp, as ONE function both call sites use.

    It was written out twice, once per call site, with the comparison inline. The tests then
    reimplemented it a third time -- which is how a prefetch query that omitted the anchor stayed
    invisible: every test computed the clamp itself from a row it had selected itself, so none of
    them ever ran the production query that was missing the column.

    `renewable` is the caller's answer to the ownership question, not one this asks: whether a lease
    is verifiable needs the database, and this must stay callable from a row alone.
    """
    anchor = _iso_to_epoch(_turn_anchor(state_row))
    touched = _iso_to_epoch(_last_touch(state_row))
    return turn_is_still_live(
        started_epoch=anchor,
        touched_epoch=touched,
        renewable=renewable,
        now_epoch=datetime.now(timezone.utc).timestamp(),
        strict_seconds=TURN_BUSY_BACKSTOP_SECONDS,
        absolute_max_seconds=TURN_LEASE_ABSOLUTE_MAX_SECONDS,
    )


def _last_touch(state_row) -> str:
    """When something last happened to this row -- the moving column, used only as a renewal."""
    if not state_row:
        return ""
    try:
        return str(state_row["last_event_at"] or "")
    except (KeyError, IndexError):
        return ""


async def _in_turn_survives(db, agent_id: str, state_row, *, status_signals=None,
                            turn_row=None, renewable=None) -> bool:
    """The clamp with the ownership question answered, ON THE CARRIER DELIVERY USES.

    TWO TABLES CARRY THIS TURN'S CLOCKS AND THEY CAN DISAGREE. `agent_status_state` holds the status
    engine's start and last-event; `agent_turn_state` holds the harness signal's start and last
    touch. Delivery ages the second pair. A previous round shared the policy FUNCTION and the
    renewal VERDICT and still evaluated status on the first pair -- which is not the same answer,
    and the discriminators are reachable whenever one writer updates one table without the other:

        live bridge, status touched NOW, turn-state touched 31m ago -> status renews, delivery ends
        live bridge, status touched 31m ago, turn-state touched NOW -> delivery renews, status ends

    So the STRICT arm keeps the status anchor -- that is this engine's own record of when its turn
    began, and it is what an unverified turn must age against -- while the RENEWAL arm switches to
    the authoritative `agent_turn_state` start/touch/owner tuple, exactly as `_turn_busy_holds_
    delivery` does. Once a claim is being trusted, both readers trust the same evidence.

    The 110-combination grid proves the policy is monotone in `renewable`; it says nothing about two
    tables carrying different timestamps. That is why the strict-first shortcut is safe only within
    ONE evidence tuple, and why the renewal arm re-evaluates rather than reusing the strict verdict.

    `turn_row` and `renewable` let a caller that already has them pass them in, so the served path
    does not re-read the row or repeat the ownership query it has just performed.
    """
    if _in_turn_survives_the_ceiling(state_row):
        return True
    if turn_row is None:
        turn_row = await status_signals_or_live(status_signals).turn_state(db, agent_id)
    if not turn_row:
        return False
    keys = turn_row.keys()
    owner = str((turn_row["turn_bridge_id"] if "turn_bridge_id" in keys else "") or "")
    if not owner:
        return False
    if renewable is None:
        renewable = await _turn_lease_is_renewable(db, agent_id, owner)
    if not renewable:
        return False
    return turn_is_still_live(
        started_epoch=_iso_to_epoch(str(
            (turn_row["turn_started_at"] if "turn_started_at" in keys else "") or "")),
        touched_epoch=_iso_to_epoch(str(turn_row["turn_updated_at"] or "")),
        renewable=True,
        now_epoch=datetime.now(timezone.utc).timestamp(),
        strict_seconds=TURN_BUSY_BACKSTOP_SECONDS,
        absolute_max_seconds=TURN_LEASE_ABSOLUTE_MAX_SECONDS,
    )


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
        "SELECT in_turn, awaiting_input, last_event, last_event_at, turn_started_at "
        "FROM agent_status_state WHERE agent_id=?", (aid,))).fetchone()
    in_turn = bool(st and st["in_turn"])
    awaiting_stored = bool(st and st["awaiting_input"])
    # status v2 (Fix B, 2026-06-05): in_turn staleness backstop. The OLD engine
    # clamps a stuck `working` via TURN_BUSY_BACKSTOP_SECONDS, but the NEW engine
    # had NO ceiling on in_turn — so an agent with a turn-START signal but a
    # DROPPED/absent turn-END (e.g. resident hermes, which has a start hook but no
    # end hook) would latch `working` forever. Treat in_turn as ended once the
    # row's last_event_at is older than the same backstop (dropped-event safety).
    # ANCHORED TO THE TURN'S START, not to its last touch. This aged against `last_event_at` until
    # 2026-08-31, and the hermes hook path applies a `turn_start` event before EVERY model call --
    # which refreshes exactly that column. So the ceiling that exists to clear a latched `working`
    # was measuring a clock the latch itself keeps winding, and never fired. Same defect as the
    # delivery gate's, in the table the dashboard reads. `turn_started_at` is stamped on the
    # not-busy -> busy transition and left alone, so it is the one clock a re-stamping poster cannot
    # postpone. It falls back to `last_event_at` only for a row written before the column existed,
    # which the boot backfill also repairs.
    if in_turn and not await _in_turn_survives(db, aid, st):
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


# Poll-load fix (2026-06-18): a settled `offline` agent's cached status only changes via an
# explicit cache-invalidating event (a returning heartbeat/turn/operator action all DELETE the
# row). Its refresh_after is otherwise `last_seen + liveness`, which is ANCIENT for a long-dead
# agent — so every roster poll re-derived + re-PERSISTED every offline agent, saturating SQLite's
# single writer (observed: 16/29 agents permanently expired -> sustained `database is locked`).
# Give offline a moderate future horizon so the hot read path serves cache; the reconcile sweep
# still re-validates each offline agent ~every interval (env-return safety), and recovery is
# immediate via invalidation. Tune via the agent_offline_revalidate_seconds setting.
OFFLINE_CACHE_REVALIDATE_SECONDS = 180


async def _compute_live_status_cache(db, agent_row, *, settings: Optional[dict[str, Any]] = None, now: Optional[str] = None, environments_by_machine=None, session_environment_by_agent=None, status_signals=None) -> dict[str, Any]:
    settings = settings or await _load_settings(db)
    now = now or _now()
    # An absent prefetch means READ, never means skip -- see status_signal_prefetch for the
    # measurement that motivated the batch path (61% of a reconcile pass at 40 agents).
    signals = status_signals_or_live(status_signals)
    manual_status = str(agent_row["status"] or "").strip().lower()
    if manual_status in _MANUAL_STATUSES:
        return {
            "status": manual_status,
            "reason": _row_status_note(agent_row),
            "environment_id": "",
            "session_id": "",
            "terminal_id": "",
            "active_run_id": "",
            "refresh_after": "9999-12-31T23:59:59Z",
            "updated_at": now,
        }
    session_row = await _current_agent_session_row(db, agent_row["id"])
    active_run = await _current_active_run_row(db, agent_row["id"])
    channel_pending_reply_run = await _current_channel_awaiting_reply_run_row(db, agent_row["id"])
    (turn_busy, turn_runtime, turn_updated_at, turn_state_ready,
     _si_turn_row, _si_renewable) = await _status_turn_signals(
        db, agent_row, status_signals=status_signals,
    )
    # Console-working lease (2026-06-05): a fresh spinner-gated lease is the managed-claude
    # "working" signal the per-completed-message transcript can't see (a long thinking phase
    # shows the last ENDED message). Read it HERE, but fold it into turn_busy / the v2 in_turn
    # input only AFTER worker liveness is known (below) — gated on a live worker so it can
    # never manufacture `working` for a dead/available agent (additive-only contract).
    console_working_lease = False
    console_lease_iso = ""
    subagents_active = False
    try:
        _cw = await signals.console_signal(db, agent_row["id"])
        if _cw:
            _cw_iso = str(_cw["working_at"] or "").strip()
            _seen = _iso_to_epoch(_cw_iso)
            if _seen and datetime.now(timezone.utc).timestamp() - _seen <= CONSOLE_WORKING_LEASE_SECONDS:
                console_working_lease = True
                console_lease_iso = _cw_iso
            # Subagents mini-tag (2026-06-11): the bridge stamps subagents_at while the
            # claude background-agents manager shows a RUNNING row. Same TTL as the lease.
            _sa_seen = _iso_to_epoch(str(_cw["subagents_at"] or "").strip()) if "subagents_at" in _cw.keys() else 0
            if _sa_seen and datetime.now(timezone.utc).timestamp() - _sa_seen <= CONSOLE_WORKING_LEASE_SECONDS:
                subagents_active = True
    except Exception:
        console_working_lease = False
    runtime_state = _json_loads_or(agent_row["runtime_state"], {})
    environment_id = str((session_row["environment_id"] if session_row else "") or runtime_state.get("environmentId") or "").strip()
    env_row = None
    env_status = ""
    env_bridge_id = ""
    env_last_seen = ""
    if environment_id:
        env_row = await (await db.execute("SELECT * FROM environments WHERE id = ?", (environment_id,))).fetchone()
        env_last_seen = str((env_row["last_seen"] if env_row else "") or "").strip()
        env_status = _environment_effective_status(env_row, offline_seconds=max(30, int(settings.get("environment_offline_seconds", 90) or 90))) if env_row else "offline"
        env_bridge_id = str((env_row["bridge_id"] if env_row else "") or "").strip()
    session_id = str((session_row["id"] if session_row else "") or "").strip()
    terminal_id = str((session_row["terminal_id"] if session_row and "terminal_id" in session_row.keys() else "") or "").strip()
    session_status = str((session_row["status"] if session_row else "") or "").strip().lower()
    terminal_status = str((session_row["terminal_status"] if session_row and "terminal_status" in session_row.keys() else "") or "").strip().lower()
    session_bridge_id = str((session_row["owner_bridge_id"] if session_row and "owner_bridge_id" in session_row.keys() else "") or "").strip()
    agent_last_seen = str(agent_row["last_seen"] or "").strip()
    # A live session stays reachable across bridge restarts: a new bridge
    # instance for the same environment re-adopts it on the next dispatch
    # claim, and dispatch routing safety is enforced separately by the
    # superseded-bridge checks. So a bridge-instance id change must NOT by
    # itself mark a running session offline -- only genuine env-down or
    # heartbeat staleness should. Stale "running" rows are still caught by
    # the env-offline branch below and the heartbeat-freshness else-branch.
    live_session = session_status in _LIVE_SESSION_STATUSES
    # New status taxonomy (persistent-worker model — see
    # docs/plans/persistent-worker-status-taxonomy.md).
    # `has_live_worker` discriminates `available` (env online, no
    # worker) from `online` (worker alive, idle). The "worker" is
    # whichever runtime process actually serves dispatches:
    #   - Virtual rpc child (pi managed, hermes managed) → a
    #     terminal_session row with command in VIRTUAL_RPC_COMMAND_SET
    #     and active status.
    #   - Wrapper PTY (claude-aify, codex-aify, hermes-aify, pi-aify,
    #     omp-aify, opencode wrapper) → terminal_session whose command
    #     contains "-aify" or "opencode", with active status.
    #   - Resident without any terminal row → fall back to live_session
    #     (operator launched the wrapper outside the dashboard's
    #     terminal_sessions tracking).
    # A live agent_session ALONE is NOT enough — the bridge keeps the
    # row across worker restarts (graph-tech-lead symptom: Console
    # stopped, session row stale-running, agent should be `available`
    # not `online`).
    agent_session_mode = _normalize_session_mode(agent_row["session_mode"] or "resident")
    # status v2 (2026-06-04): capture the raw resident bridge-freshness ONCE so the
    # StatusInputs byproduct assembled below can reuse it without a second
    # _resident_bridge_is_fresh call. Mirrors _gather_status_inputs, which calls it
    # UNGATED for residents; the legacy resident_bridge_stale below stays gated on
    # the resident-run capability exactly as before (behavior-preserving).
    resident_bridge_fresh: Optional[bool] = None
    if agent_session_mode == "resident":
        resident_bridge_fresh = await _resident_bridge_is_fresh(
            db,
            agent_row,
            lease_seconds=int(settings.get("resident_lease_seconds", 150) or 150),
        )
    resident_bridge_stale = False
    if agent_session_mode == "resident" and "resident-run" in _row_capabilities(agent_row):
        resident_bridge_stale = not resident_bridge_fresh
    # fix/resident-hermes-status (2026-06-02): a resident agent whose wake-mode is
    # a `*-missing-handle` mode has NO usable wake handle (resident hermes with no
    # usable gatewayUrl; resident codex/opencode/pi with no sessionHandle) — it
    # cannot be woken at all, so it is NOT `available`. It must read `stale`,
    # CONSISTENT with the dashboard dot, which already derives a red/unreachable
    # dot from the non-live-wake wake-mode (operator-reported `available`+red+
    # "Hermes missing handle" split). NOTE: the resident_bridge_stale gate above
    # is itself gated on `"resident-run" in _row_capabilities(...)`, and
    # _row_capabilities STRIPS resident-run for a hermes with no gatewayUrl — so a
    # missing-handle resident never reaches that gate and would otherwise fall
    # through to `available`. Setting `resident_bridge_stale` HERE closes that hole
    # at the same liveness altitude — that is the flag `derive()` reads, through the
    # facts built at the end of this function. A genuinely-live resident (fresh
    # bridge + usable handle → `*-live`/`-thread-resume`) is unaffected. Excludes
    # `presence-only` (opencode/pi resident) and inbox/`message-only` agents, which
    # are not wake-handle-backed targets and have their own taxonomy.
    #
    # THIS COMMENT NAMED THE WRONG FLAG until 2026-08-29. It said "this flag closes
    # that hole" of a `resident_missing_handle` local that nothing read — so the
    # block looked like dead code with one live line hidden inside it, and deleting
    # it on that reading would have reopened the hole the paragraph above describes.
    # `test_resident_hermes_missing_handle_status.py` is what would have caught it.
    if agent_session_mode == "resident":
        _wake_mode = _agent_wake_mode(agent_row)
        if _wake_mode.endswith("-missing-handle"):
            resident_bridge_stale = True
    # has_live_worker (+ the two channel-sidecar reason flags) is now decided by
    # the SHARED _worker_liveness_for helper so the legacy derivation and the
    # event engine (_gather_status_inputs → _has_live_worker_for) can never
    # disagree on worker liveness. Behavior-preserving extraction — same inputs
    # (agent_session_mode, live_session), same result.
    _worker_live = await _worker_liveness_for(
        db, agent_row, agent_session_mode=agent_session_mode, live_session=live_session
    )
    has_live_worker = _worker_live.has_live_worker
    channel_managed_no_sidecar = _worker_live.channel_managed_no_sidecar
    channel_managed_no_console = _worker_live.channel_managed_no_console
    # FIX B (2026-06-02): a MANAGED agent can only be spawned/hosted by its OWNING
    # environment bridge. If that env bridge is offline/stale, the agent is
    # effectively offline — even when a surviving detached delivery loop keeps a
    # fresh sidecar/lease/heartbeat (which would otherwise compute `online`). The
    # operator killed the `aify-comms` env bridge and managed agents stayed
    # `available`/`online` for exactly this reason: the env-bound offline branch
    # below only fires when `environment_id` resolved from a LIVE session row /
    # runtime_state, both absent once the worker died. This gate resolves the
    # STORED owning environment (runtime_config.environmentId / machine_id+runtime
    # match) and hard-forces offline, short-circuiting the online/available
    # derivation. Resident agents are EXCLUDED — their liveness is the resident
    # bridge, not the env bridge — so a down env bridge must not force them offline.
    managed_env_bridge_offline = False
    if agent_session_mode == "managed":
        owning_env_row = await _managed_owning_environment_row(
            db, agent_row, resolved_environment_id=environment_id,
            environments_by_machine=environments_by_machine,
            session_environment_by_agent=session_environment_by_agent,
        )
        if owning_env_row is not None:
            owning_env_status = _environment_effective_status(
                owning_env_row,
                offline_seconds=max(30, int(settings.get("environment_offline_seconds", 90) or 90)),
            )
            if owning_env_status not in {"online", "degraded"}:
                managed_env_bridge_offline = True
                # Bind environment_id so the reason/offline branch below and the
                # cache row reflect the resolved owning environment.
                if not environment_id:
                    environment_id = str(owning_env_row["id"] or "").strip()
                    env_status = owning_env_status
                    env_last_seen = str((owning_env_row["last_seen"] or "")).strip()
    if managed_env_bridge_offline:
        # Owning env bridge is down → hard offline regardless of any surviving loop.
        has_live_worker = False
        effective_status = "offline"
    elif has_live_worker:
        # A live worker that is not handling a turn is public `online`.
        # `turn_state_ready` remains useful internally for readiness and cache
        # invalidation, but is not a separate user-facing agent status.
        effective_status = "online"
    elif environment_id and env_status not in {"online", "degraded"}:
        # An env IS bound but it's unreachable → offline. Unbound agents
        # (no environment_id yet) fall through to "available" — they can
        # still receive a message, the dispatch path resolves the env at
        # claim time.
        effective_status = "offline"
    else:
        effective_status = "available"
    # Fold the console-working lease into turn_busy now that worker liveness is known.
    # Gated on has_live_worker so it can NEVER manufacture `working` for a dead/available
    # agent — only a live managed worker showing its spinner reads `working` (the
    # turn_busy branch below). Additive: it never clears turn_busy.
    if console_working_lease and has_live_worker and not turn_busy:
        turn_busy = True
        if not turn_runtime:
            turn_runtime = "claude-code"
    reason = ""
    awaiting_reply = False  # set True when the agent is idle but owes a channel reply
    terminal_input_hint = ""
    if (
        _normalize_runtime(str(agent_row["runtime"] or "")) == "claude-code"
        and terminal_id
        and (active_run or (agent_session_mode == "managed" and has_live_worker))
    ):
        try:
            # THE LIVE SCREEN FIRST, and the stored tail only when this process has never seen
            # that terminal. `_terminal_prompt_hint_from_raw` renders raw -> screen -> hint, and
            # `terminal_snapshot` already holds the rendered screen in memory from the same output
            # the tail is written from -- so this skips a pyte reconstruction of up to 64 KB per
            # status refresh, not merely a SELECT. The fallback stays because the live screen is a
            # process global and is empty after a restart, which is precisely what the tail is for.
            live = render_live_screen(terminal_id)
            if live is not None:
                terminal_input_hint = _terminal_prompt_hint_from_screen(f"term:{terminal_id}", live[0])
            else:
                terminal_row = await (await db.execute(
                    "SELECT output, cols FROM terminal_sessions WHERE id = ?",
                    (terminal_id,),
                )).fetchone()
                terminal_input_hint = _terminal_prompt_hint_from_raw(
                    f"term:{terminal_id}",
                    terminal_row["output"] if terminal_row else "",
                    (terminal_row["cols"] if terminal_row and "cols" in terminal_row.keys() else 0),
                )
        except Exception:
            terminal_input_hint = ""
    # NO `active_run_runtime`. It was computed here on every status refresh and read at no line in
    # the service; `active_run_mode` beside it IS read, which is how it survived.
    active_run_mode = str(active_run["dispatch_mode"] or "").strip().lower() if active_run else ""
    active_run_terminal_missing = (
        active_run
        and active_run_mode == "terminal"
        and (not terminal_id or terminal_status not in _TERMINAL_ACTIVE_STATUSES)
    )
    # ONE console-boot read for this agent, shared with the display-parity line further down. Both
    # sites are guarded and often neither fires, so it stays lazy — this only stops the SECOND read.
    console_booting_once = ConsoleBootingOnce(db, agent_row["id"])
    effective_status, reason, awaiting_reply = await _decide_effective_status(
        db,
        StatusFacts(
            active_run=active_run,
            active_run_terminal_missing=active_run_terminal_missing,
            agent_row=agent_row,
            agent_session_mode=agent_session_mode,
            channel_managed_no_console=channel_managed_no_console,
            channel_managed_no_sidecar=channel_managed_no_sidecar,
            channel_pending_reply_run=channel_pending_reply_run,
            env_bridge_id=env_bridge_id,
            env_status=env_status,
            environment_id=environment_id,
            has_live_worker=has_live_worker,
            live_session=live_session,
            managed_env_bridge_offline=managed_env_bridge_offline,
            resident_bridge_stale=resident_bridge_stale,
            session_bridge_id=session_bridge_id,
            session_status=session_status,
            terminal_input_hint=terminal_input_hint,
            terminal_status=terminal_status,
            turn_busy=turn_busy,
            turn_runtime=turn_runtime,
        ),
        effective_status,
        reason,
        awaiting_reply,
        console_booting_once,
    )
    # NOTE (2026-06-05): a managed agent whose last session ended FAILED stays `available` by
    # design — it lazy-respawns on the next send (genuinely available-to-retry, NOT blocked; see
    # test_managed_codex_online_from_fresh_wrapper_child_bridge). The originally-reported
    # "stopped · Console attached" was a TRANSIENT teardown race during a hermes resume error,
    # removed at the root by the DB-validated resume fix (5c1617a); the dashboard console label
    # is the honest surface (never "attached" for a dead session — Dashboard Next).
    refresh_after = _status_refresh_after(
        agent_last_seen,
        env_last_seen,
        liveness_seconds=int(settings.get("agent_liveness_seconds", 90) or 90),
        env_offline_seconds=max(30, int(settings.get("environment_offline_seconds", 90) or 90)),
    )
    # When `working` is driven by a fresh turn_busy (NOT an active run, which has
    # its own lifecycle), clamp refresh_after to the turn-busy BACKSTOP window so a
    # DROPPED turn-end event self-heals at the single long ceiling (~15m) instead of
    # waiting out the 5-30min heartbeat windows. WS5 Task 5.2/5.3: the normal off-
    # working transition is the turn-END EVENT (which invalidates the cache
    # immediately via /turn-end), so this clamp is purely the dropped-event
    # backstop. `active_run` working is intentionally left untouched.
    if effective_status == "working" and turn_busy and not active_run and turn_updated_at:
        busy_deadline = _iso_add_seconds(turn_updated_at, TURN_BUSY_BACKSTOP_SECONDS)
        if busy_deadline:
            refresh_after = min([v for v in (refresh_after, busy_deadline) if v])
    # (Turn-end grace removed 2026-06-19 — pure-event; see the turn_busy block above.)
    # M2: when `working` is driven by the console-working lease (turn_updated_at is unset,
    # so the backstop clamp above is skipped), clamp refresh_after to the lease TTL so the
    # cache self-expires when the spinner stops — the bridge stops POSTing, so nothing else
    # forces a recompute, and the cached `working` would otherwise persist to the next
    # heartbeat window (minutes) rather than the 12s lease.
    if effective_status == "working" and console_working_lease and console_lease_iso:
        lease_deadline = _iso_add_seconds(console_lease_iso, CONSOLE_WORKING_LEASE_SECONDS)
        if lease_deadline:
            refresh_after = min([v for v in (refresh_after, lease_deadline) if v])
    # POLL-LOAD FIX (2026-06-18): a settled `offline` agent computes refresh_after from
    # agent_last_seen + liveness — ANCIENT for a long-dead agent, so it is PERMANENTLY expired
    # and gets re-derived + re-persisted on EVERY roster poll (GET /agents | /sessions), a
    # write storm that saturated the single SQLite writer (sustained `database is locked`).
    # An offline agent needs no poll-driven recompute: its status only changes via an explicit
    # cache-invalidating event (heartbeat/turn/operator action -> _invalidate_agent_live_state).
    # Push refresh_after to a moderate future horizon so the hot read path serves cache; the
    # reconcile sweep still re-validates it each horizon (env-return safety), recovery on any
    # real event is immediate via invalidation. (`stopped`/manual already short-circuit at the
    # top with a 9999 horizon.)
    if effective_status == "offline":
        offline_revalidate = int(settings.get("agent_offline_revalidate_seconds", OFFLINE_CACHE_REVALIDATE_SECONDS) or OFFLINE_CACHE_REVALIDATE_SECONDS)
        horizon = _iso_add_seconds(now, max(60, offline_revalidate))
        if horizon:
            refresh_after = horizon
    # status v2 (2026-06-04): assemble the engine's StatusInputs from the raw
    # signals THIS function already computed, so _refresh_agent_live_state can
    # derive the `new` status with a PURE derive() call instead of re-running the
    # full _gather_status_inputs double-gather (the 10x idle-CPU regression). This
    # MUST produce the same StatusInputs _gather_status_inputs does — the field
    # semantics below mirror it exactly (see _gather_status_inputs).
    #   - mode/disabled: same source rows.
    #   - in_turn/awaiting_input: one cheap indexed agent_status_state lookup (the
    #     SAME table _gather_status_inputs reads; the legacy derivation above uses
    #     agent_turn_state.turn_busy instead, so this single query is required).
    #   - worker_present (managed): the already-computed `has_live_worker` local —
    #     the SHARED _worker_liveness_for result, identical to _has_live_worker_for,
    #     so the expensive worker re-scan is eliminated.
    #   - env_reachable (managed): resolved exactly as _gather_status_inputs (owning
    #     env row with resolved_environment_id="" -> effective status in online/
    #     degraded). A cheap indexed env lookup, NOT the expensive worker re-scan.
    #   - resident liveness: the `resident_bridge_fresh` local captured above (the
    #     SAME _resident_bridge_is_fresh call _gather_status_inputs makes, computed
    #     once and reused).
    _si_st = await signals.status_state(db, agent_row["id"])
    # M-B parity (2026-06-05): mirror the _gather_status_inputs in_turn staleness backstop
    # (Fix B) here too. This byproduct is the SERVED path under status_engine=new; without
    # the clamp a DROPPED/absent turn-END would latch `working` here forever while the
    # authoritative _gather_status_inputs would correctly clear it past the backstop — so the
    # "MUST produce the same StatusInputs" promise above would be violated for stale in_turn.
    _si_raw_in_turn = bool(_si_st and _si_st["in_turn"])
    # The SAME function as the authoritative path above, not a second copy of its arithmetic. Two
    # clamps on one question that computed it separately is how the parity this docstring promises
    # quietly stops holding.
    if _si_raw_in_turn and not await _in_turn_survives(
            db, agent_row["id"], _si_st, status_signals=status_signals,
            turn_row=_si_turn_row, renewable=_si_renewable):
        _si_raw_in_turn = False
    # H1: the console-working lease must feed BOTH engines. The v2 engine reads in_turn from
    # agent_status_state (which the lease never writes), so OR the worker-gated lease in here
    # too — otherwise the feature is a no-op under status_engine=new. (The lease has its OWN
    # short TTL, so OR-ing it after the staleness clamp can't resurrect a truly-stale turn.)
    _si_in_turn = _si_raw_in_turn or (console_working_lease and has_live_worker)
    _si_awaiting = bool(_si_st and _si_st["awaiting_input"])
    # WS-5 parity: compute the awaiting-input signal via the SAME helper _gather_status_inputs
    # uses (NOT the legacy terminal_input_hint above, which keys on the bound terminal_id) so
    # both StatusInputs builders agree. Gated on _si_in_turn (blocked only applies mid-turn).
    if _si_in_turn and not _si_awaiting:
        _si_awaiting = await _agent_awaiting_input(db, agent_row["id"])
    # Mirrors _gather_status_inputs exactly (the byproduct-parity promise): disabled =
    # stopped OR wake disabled (launch_mode='none') — see the 2026-06-12 audit note there.
    _si_disabled = (
        str(agent_row["status"] or "").lower() == "stopped"
        or str(agent_row["launch_mode"] or "").lower() == "none"
    )
    if agent_session_mode == "managed":
        _si_env_row = await _managed_owning_environment_row(db, agent_row, resolved_environment_id="", session_environment_by_agent=session_environment_by_agent,
                                                              environments_by_machine=environments_by_machine)
        _si_env_reachable = _managed_env_reachable(agent_row, _si_env_row, settings)
        # WS-12 parity: booting-console → display online (same helper as _gather_status_inputs).
        _si_console_booting = (
            not has_live_worker and _si_env_reachable
            and await console_booting_once.value()
        )
        # M2 (external review 2026-08-18): these two were MISSING here while `_gather_status_inputs`
        # set both, so the cheap path — which is the SERVED one for GET /agents — derived `available`
        # for an agent the authoritative path called `misconfigured` or `starting`. False-available on
        # the primary roster path is a routing bug, not a cosmetic mismatch: `available` is documented
        # as deliverable and promises a cold start, so a send was accepted for an agent that could not
        # take it. The producer-agreement test did not catch it because no case produced either state
        # — the fields agreed at their defaults.
        #
        # Computed under the same guards as the authoritative builder, in the same order, so the two
        # cannot disagree by construction rather than by luck.
        _si_config_defect = ""
        if not has_live_worker and _si_env_reachable and not _si_console_booting:
            _si_config_defect = await _agent_config_defect(db, agent_row, agent_session_mode)
        _si_spawn_starting = (
            not has_live_worker and _si_env_reachable and not _si_console_booting
            and not _si_config_defect
            and await _managed_spawn_is_starting(db, agent_row["id"])
        )
        status_inputs = StatusInputs(
            mode=agent_session_mode, alive=has_live_worker, in_turn=_si_in_turn,
            awaiting_input=_si_awaiting, worker_present=has_live_worker,
            env_reachable=_si_env_reachable, disabled=_si_disabled,
            bridge_stale=False, has_live_session=has_live_worker,
            console_booting=_si_console_booting,
            config_defect=_si_config_defect,
            spawn_starting=_si_spawn_starting,
        )
    else:
        _si_fresh = bool(resident_bridge_fresh)
        # Phase I flip parity (see _gather_status_inputs): a *-missing-handle resident → stale.
        _si_missing_handle = str(_agent_wake_mode(agent_row) or "").endswith("-missing-handle")
        status_inputs = StatusInputs(
            mode=agent_session_mode, alive=_si_fresh, in_turn=_si_in_turn,
            awaiting_input=_si_awaiting, worker_present=_si_fresh,
            env_reachable=True, disabled=_si_disabled,
            bridge_stale=(not _si_fresh) or _si_missing_handle, has_live_session=_si_fresh,
            console_booting=False,
            # config_defect is DELIBERATELY NOT SET on the resident side, and this is a pinned open
            # question rather than an oversight. `_gather_status_inputs` DOES set it for a
            # `*-missing-handle` resident, so the two producers genuinely disagree here: the
            # authoritative path derives `misconfigured`, this one derives `offline`.
            #
            # Making them agree is a one-line change and I have not made it, because it moves every
            # resident-without-a-wake-handle from the unreachable family into `misconfigured`, and
            # `test_resident_hermes_missing_handle_status` asserts the dashboard DOT and the label
            # agree within that family — so the badge for a whole class of agents changes colour.
            # comms-senior-dev ruled M2 belongs in the same slice as the 10a `available` semantics,
            # which is still awaiting an operator ruling; this is that slice's work, not a fix to
            # slip in behind it.
            #
            # `test_status_inputs_producers_agree` pins the divergence explicitly so it cannot be
            # forgotten, and so whoever settles 10a is told to delete the pin.
        )
    # Subagents mini-tag (2026-06-11): surfaced through the reason string (the dashboard
    # already derives nuances like awaiting-reply from it) so no payload-shape change.
    if subagents_active and effective_status == "working":
        reason = f"{reason} Running subagents.".strip()
    return {
        "status": effective_status,
        "reason": reason,
        "awaiting_reply": awaiting_reply,
        "environment_id": environment_id,
        "session_id": session_id,
        "terminal_id": terminal_id,
        "active_run_id": str((active_run["id"] if active_run else "") or "").strip(),
        "refresh_after": refresh_after,
        "updated_at": now,
        "status_inputs": status_inputs,
    }
