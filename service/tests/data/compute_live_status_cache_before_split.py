"""`_compute_live_status_cache` exactly as it was before the decision-block split.

The service's status derivation, 551 lines, and the reference the inline-back proof compares against.

Committed as a FIXTURE, captured with an EXPLICIT utf-8 decode. Both rules were learned by breaking
them; see `service/tests/data/register_agent_before_split.py`.

NOT AN IMPORTABLE MODULE — a function lifted out of its module reads names that were in scope there.
"""

async def _compute_live_status_cache(db, agent_row, *, settings: Optional[dict[str, Any]] = None, now: Optional[str] = None) -> dict[str, Any]:
    settings = settings or await _load_settings(db)
    now = now or _now()
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
    # Authoritative mid-turn signal pushed by the bridge (contract). Fresh
    # turn_busy=1 means the runtime is executing a turn right now → working,
    # even when the dispatch row is delivered/ambiguous. Stale (no refresh
    # within TURN_BUSY_STALE_SECONDS) is treated as not-busy.
    turn_busy = False
    turn_runtime = ""
    turn_updated_at = ""
    # Plan 4 task 12 (2026-05-25): `ready` is the bridge-pushed
    # handshake-complete signal. It remains an internal readiness bit; the
    # public idle-live status is `online` so operators do not see both
    # `ready` and `available` as competing positive states.
    turn_state_ready = False
    try:
        _tb = await (await db.execute(
            "SELECT turn_busy, turn_runtime, turn_updated_at, ready FROM agent_turn_state WHERE agent_id = ?",
            (agent_row["id"],),
        )).fetchone()
        if _tb:
            if int(_tb["turn_busy"] or 0) == 1:
                _age = datetime.now(timezone.utc).timestamp() - _iso_to_epoch(str(_tb["turn_updated_at"] or ""))
                # WS5 Task 5.2/5.3: STATUS staleness uses the LONG backstop. The
                # turn-END event (POST /turn-end) is the primary clear; this window
                # only catches a DROPPED event, so it sits at the single long
                # wall-clock ceiling rather than racing the re-pulse cadence.
                if _iso_to_epoch(str(_tb["turn_updated_at"] or "")) and _age <= TURN_BUSY_BACKSTOP_SECONDS:
                    turn_busy = True
                    turn_runtime = str(_tb["turn_runtime"] or "").strip()
                    turn_updated_at = str(_tb["turn_updated_at"] or "").strip()
            # PURE-EVENT (2026-06-19): the turn-end GRACE (#224, 20s) was REMOVED. It held
            # `working` for 20s after turn_busy cleared to mask a managed claude's premature/
            # duplicate Stop hooks — a TIME-BASED hold that (a) stacked on the hermes bridge's
            # 9s idle-debounce to show "working" ~30s after a real idle (operator-reported), and
            # (b) is exactly the time-decay the status engine must not have. The flap is now
            # fixed AT THE SOURCE: the bridge turn detectors (hermes gateway / claude transcript)
            # only clear turn_busy on EVENT-confirmed end, and run fast enough to re-assert a
            # premature clear within a tick. Status here is pure-event: turn_busy=1 (within the
            # far 30-min wedged-bridge backstop) AND live → working; otherwise online.
            try:
                turn_state_ready = int(_tb["ready"] or 0) == 1
            except (IndexError, KeyError):
                # Pre-migration row (column absent on a foreign DB schema).
                turn_state_ready = False
    except Exception:
        turn_busy = False
        turn_state_ready = False
    # Console-working lease (2026-06-05): a fresh spinner-gated lease is the managed-claude
    # "working" signal the per-completed-message transcript can't see (a long thinking phase
    # shows the last ENDED message). Read it HERE, but fold it into turn_busy / the v2 in_turn
    # input only AFTER worker liveness is known (below) — gated on a live worker so it can
    # never manufacture `working` for a dead/available agent (additive-only contract).
    console_working_lease = False
    console_lease_iso = ""
    subagents_active = False
    try:
        _cw = await (await db.execute(
            "SELECT working_at, subagents_at FROM agent_console_signal WHERE agent_id = ?",
            (agent_row["id"],),
        )).fetchone()
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
    # through to `available`. This flag closes that hole at the same liveness
    # altitude. A genuinely-live resident (fresh bridge + usable handle →
    # `*-live`/`-thread-resume`) is unaffected. Excludes `presence-only`
    # (opencode/pi resident) and inbox/`message-only` agents, which are not
    # wake-handle-backed targets and have their own taxonomy.
    resident_missing_handle = False
    if agent_session_mode == "resident":
        _wake_mode = _agent_wake_mode(agent_row)
        if _wake_mode.endswith("-missing-handle"):
            resident_missing_handle = True
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
            db, agent_row, resolved_environment_id=environment_id
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
    active_run_runtime = _normalize_runtime(str(active_run["runtime"] or "")) if active_run else ""
    active_run_mode = str(active_run["dispatch_mode"] or "").strip().lower() if active_run else ""
    active_run_terminal_missing = (
        active_run
        and active_run_mode == "terminal"
        and (not terminal_id or terminal_status not in _TERMINAL_ACTIVE_STATUSES)
    )
    if managed_env_bridge_offline:
        # FIX B: owning env bridge is down — hard offline takes precedence over the
        # active-run/terminal derivations below (only the env bridge can host the
        # worker, so any surviving run is moot).
        effective_status = "offline"
        reason = (
            f'Owning environment "{environment_id}" is {env_status or "offline"}; '
            "only its bridge can host this managed worker."
        )
    elif active_run_terminal_missing:
        effective_status = "blocked"
        reason = f'Managed terminal-backed active run has no live terminal backing. Active run: {active_run["subject"] or active_run["id"]}.'
    elif (
        environment_id
        and env_status
        and env_status not in {"online", "degraded"}
        and not (agent_session_mode == "resident" and not resident_bridge_stale)
    ):
        effective_status = "offline"
        reason = f'Environment "{environment_id}" is {env_status}.'
    elif (
        agent_session_mode != "managed"
        and session_bridge_id
        and env_bridge_id
        and session_bridge_id != env_bridge_id
        and not live_session
        and not active_run
    ):
        # STATUS POLICY (2026-06-04): a MANAGED agent is `offline` ONLY when it is
        # disabled/stopped OR its owning environment is unreachable (both handled
        # above: managed_env_bridge_offline + the env-unreachable branches). An
        # orphaned session row whose owning bridge != the current env bridge just
        # means the previous WORKER died — with a reachable env the agent is still
        # lazy-autostartable, so it must rest at `available` (the base derivation at
        # ~L4041), NOT be demoted to offline here. Excluding managed keeps this
        # branch for resident-style sessions, whose liveness is their own bridge.
        effective_status = "offline"
        reason = "Current environment bridge no longer owns the active session."
    elif resident_bridge_stale and not active_run:
        # An expired resident bridge means a DEAD worker → `offline` (the proof-based
        # rewrite dropped the resident-only `stale` label; a lapsed bridge lease IS
        # offline), even when the agent owes a channel reply. (Previously `and not
        # channel_pending_reply_run`
        # suppressed this so the channel-pending branch could manufacture `online`
        # for a dead agent — the FIX-3 bug. The channel-pending branch now refuses
        # to upgrade a dead worker, so this stale derivation is the correct landing.)
        #
        # pure-event-status change #2 (2026-06-02): liveness wins over turn_busy.
        # The `and not turn_busy` guard was REMOVED here. With STATUS now pure-event
        # (the short status window is gone — change #3), a DEAD resident stuck with a
        # lingering turn_busy=1 (a missed turn-end on a now-dead worker) would have
        # SKIPPED this stale branch and fallen into `elif turn_busy → working`, i.e.
        # working-forever. The resident bridge lease (150s, _resident_bridge_is_fresh)
        # is the liveness signal: an expired bridge is a dead worker regardless of any
        # turn_busy=1, so it must derive offline BEFORE the turn_busy branch is reached.
        effective_status = "offline"
        reason = "Resident bridge heartbeat is gone; restart the resident wrapper or switch to managed."
    # A console terminal reaching an end state returns ownership to managed (the
    # runtime contract reverts owner_mode to managed on stop/fail). So it is a
    # fallback-to-managed candidate, not final unavailability: fall through to
    # active-run / heartbeat-freshness, which is the real source of truth.
    elif active_run and terminal_input_hint:
        effective_status = "blocked"
        reason = f'{terminal_input_hint} Active run: {active_run["subject"] or active_run["id"]}.'
    elif (
        agent_session_mode == "managed"
        and has_live_worker
        and terminal_input_hint
        and terminal_status in _TERMINAL_ACTIVE_STATUSES
    ):
        effective_status = "blocked"
        reason = terminal_input_hint
    elif active_run:
        effective_status = "working"
        reason = f'Active run: {active_run["subject"] or active_run["id"]}.'
    elif turn_busy:
        effective_status = "working"
        reason = f"Executing turn ({turn_runtime})." if turn_runtime else "Executing turn."
    elif channel_pending_reply_run:
        # Status-split (2026-05-31): reaching this branch means NOT active_run
        # and NOT turn_busy — the turn ENDED, the agent is IDLE but owes a reply.
        # That is NOT "working" (actively computing) — showing orange working for
        # an idle agent was the operator-reported "blink when not working". It is
        # `online` with an `awaitingReply` flag (the reminder loop nudges it; the
        # Work Loop tracks the open contract). `working` is reserved for a fresh
        # turn_busy or a claimed/running run. NOTE: the runtime's own turn-end
        # signal (claude Stop hook / hermes post_llm_call / codex turn/completed /
        # pi agent_end) clears turn_busy precisely; this branch is the
        # idle-owes-reply state after that.
        # FIX (2026-06-01): only show `online` when the worker is actually live.
        # A DEAD worker that owes a reply must NOT be manufactured into `online`
        # (visible-TUI truthfulness): a managed claude with a dead console/sidecar
        # has has_live_worker=False (status-F1), and a resident with a stale bridge
        # is positively dead. In either case fall through so the
        # available/stale/offline derivation below stands. A live resident with no
        # tracked terminal row (resident_bridge_stale=False, has_live_worker may be
        # False) is NOT dead and keeps the online-awaiting-reply state.
        worker_is_dead = (
            (agent_session_mode == "managed" and not has_live_worker)
            or resident_bridge_stale
        )
        if not worker_is_dead:
            awaiting_reply = True
            if effective_status not in {"offline", "blocked"}:
                effective_status = "online"
            reason = (
                f'Idle — awaiting reply: '
                f'{channel_pending_reply_run["subject"] or channel_pending_reply_run["id"]}.'
            )
    elif session_status in {"recovering", "restarting"} or terminal_status == "stopping":
        effective_status = "working"
        reason = session_status or terminal_status or "Session is transitioning."
    # NOTE: "working" deliberately requires a tracked active run/turn (or a
    # genuine recover/restart transition) — NOT console attachment or console
    # byte activity. Long-lived managed consoles emit ambient output (prompt
    # redraws, keepalives) while the agent is idle; treating that as "working"
    # made idle agents show working forever. An attached-but-runless console
    # is reachable, so it falls through to the heartbeat branch as "active",
    # never "working". (Supersedes the B1 / console-activity heuristics.)
    else:
        # Proof-based rewrite (2026-06-18): the time-decay staleness block that lived
        # here (idle_minutes→`idle`, offline_minutes→`offline`) was REMOVED. It only ever
        # set `effective_status`, which is a byproduct overridden by derive() — and derive()
        # (the authority) does NOT demote a live-but-quiet agent by wall-clock minutes:
        # `offline` comes from worker/bridge liveness, and `idle` no longer exists. Heartbeat
        # liveness is enforced by `refresh_after` (agent_liveness_seconds) + has_live_worker,
        # not a minute threshold here.
        # Task 1.6: surface WHY a channel-enabled managed agent is only
        # `available` rather than deliverable — the channel sidecar
        # (hermes-channel.js) is not heartbeating. Only annotate when we
        # haven't already attached a more specific reason (e.g. offline).
        if effective_status == "available" and channel_managed_no_console and not reason:
            reason = "Worker has no visible console (headless orphan being reaped)."
        elif effective_status == "available" and channel_managed_no_sidecar:
            # BOOT vs DEAF (2026-06-05, operator-chosen): a live console whose sidecar hasn't
            # registered SINCE THE CONSOLE STARTED is BOOTING → DISPLAY `online` so the operator
            # doesn't miss the terminal. A console whose sidecar registered then died stays
            # `available` (not deliverable; 13c4ae8). DISPLAY-ONLY — has_live_worker is unchanged,
            # so a send during boot still QUEUES until the sidecar claims (routing untouched).
            # (Legacy-path display; live engine is `old`. A `status_engine=new` flip would need
            # the same signal in StatusInputs for parity.)
            if await _managed_console_is_booting(db, agent_row["id"]):
                effective_status = "online"
                if not reason:
                    reason = "Console booting (worker starting; deliverable once it claims)."
            elif not reason:
                reason = "No live channel sidecar heartbeat (not deliverable)."
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
    _si_st = await (await db.execute(
        "SELECT in_turn, awaiting_input, last_event_at FROM agent_status_state WHERE agent_id=?",
        (agent_row["id"],),
    )).fetchone()
    # M-B parity (2026-06-05): mirror the _gather_status_inputs in_turn staleness backstop
    # (Fix B) here too. This byproduct is the SERVED path under status_engine=new; without
    # the clamp a DROPPED/absent turn-END would latch `working` here forever while the
    # authoritative _gather_status_inputs would correctly clear it past the backstop — so the
    # "MUST produce the same StatusInputs" promise above would be violated for stale in_turn.
    _si_raw_in_turn = bool(_si_st and _si_st["in_turn"])
    if _si_raw_in_turn:
        _si_last_event_epoch = _iso_to_epoch(_si_st["last_event_at"] if _si_st else "")
        if _si_last_event_epoch and (
            datetime.now(timezone.utc).timestamp() - _si_last_event_epoch
        ) > TURN_BUSY_BACKSTOP_SECONDS:
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
        _si_env_row = await _managed_owning_environment_row(db, agent_row, resolved_environment_id="")
        _si_env_reachable = _managed_env_reachable(agent_row, _si_env_row, settings)
        # WS-12 parity: booting-console → display online (same helper as _gather_status_inputs).
        _si_console_booting = (
            not has_live_worker and _si_env_reachable
            and await _managed_console_is_booting(db, agent_row["id"])
        )
        status_inputs = StatusInputs(
            mode=agent_session_mode, alive=has_live_worker, in_turn=_si_in_turn,
            awaiting_input=_si_awaiting, worker_present=has_live_worker,
            env_reachable=_si_env_reachable, disabled=_si_disabled,
            bridge_stale=False, has_live_session=has_live_worker,
            console_booting=_si_console_booting,
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
