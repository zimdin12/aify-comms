"""Terminal-control reconciliation: which queued controls are still actionable, and what to do
with the ones that are not.

Moved out of `service/db.py` in v0.5.4. It had no business there — `db.py` is the connection layer
and the schema, and this is a RECONCILER, which is what `service/reconcilers/` exists for. It also
took db.py to 996 lines, four short of the gate, which is how it came up.

A LEAF, in the sense the reviewer rulings use: it takes the connection as a parameter and imports
nothing from `service` at all, so it cannot participate in a cycle with `db.py` that now calls it.

WHAT IT IS FOR. A "control" is a queued instruction for a terminal — stop, resize, input. Two
conditions can make one undeliverable: the terminal is no longer active, or the environment that
owned it can no longer act. This sweep fails those, so they do not sit pending forever. The subtlety
that has cost this repo twice is that a queued STOP is exempt from the liveness half, and the rule
is implemented in TWO places — here and in `terminal_runs.py::_reconcile_ended_terminal_controls`.
Both carry the exemption or neither does; `test_stop_control_survives_reconcile.py` drives both
paths specifically so they cannot drift apart again.
"""
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite

from service.clock import ISO_SECONDS, now as _now


# ONE definition of "a bridge on this environment can still act on a queued control".
#
# N7 (reviewer finding, 2026-07-26): the sweeps below asked `environments.status = 'online'` while
# api_v2's stop-request path asks
#     _environment_effective_status(env_row, offline_seconds=max(30, setting)) in {"online","degraded"}
# (`bridge_can_claim`, then in api_v2.py). Two halves of one feature, two different answers to
# the same question, so a degraded environment's stop was left PENDING by the request path and then
# FAILED by the sweep — the PTY survived, the session was already 'ended', and Start was free to
# spawn a second worker. Same chain v0.1 fixed for a changed `bridge_id`, reached via `degraded`.
#
# So this mirrors the api_v2 derivation instead of inventing a third one:
#   * `degraded` counts — eleven reachability gates in api_v2 accept it and
#     `_ENVIRONMENT_HEARTBEAT_STATUSES` keeps it heartbeating; a degraded bridge is reduced-capability,
#     not dead;
#   * BOTH heartbeat statuses AGE. Raw `status='online'` never aged, so a silent online bridge kept
#     its controls pending indefinitely while api_v2 already called that environment offline. Ageing
#     is what preserves the accumulation bound once `degraded` is admitted.
#   * `offline`/`forgotten`/`disabled` are DECISIONS, not observations, and are never revived here.
#
# Degenerate `last_seen` values are enumerated deliberately (the class that produced the future-
# timestamp strands): an absent, empty, malformed or non-canonical stamp is NOT datable, so it TRUSTS
# the stored status rather than inventing a failure — exactly what `_environment_effective_status`
# does when `fromisoformat` raises. Comparison is on the canonical 19-char prefix, so both
# `...:00Z` and a legacy `...:00.123456Z` compare correctly (C2: never compare mixed-width
# timestamps lexically).
_ENV_CANONICAL_TS_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]"


def _environment_actionable_sql() -> str:
    """SQL fragment: the `environments` row in scope can still act. Binds ONE `?` (the cutoff)."""
    return (
        "environments.status IN ('online', 'degraded')\n"
        "                AND (\n"
        "                    environments.last_seen IS NULL\n"
        "                    OR substr(environments.last_seen, 1, 19) NOT GLOB '" + _ENV_CANONICAL_TS_GLOB + "'\n"
        "                    OR substr(environments.last_seen, 1, 19) >= substr(?, 1, 19)\n"
        "                )"
    )


async def _environment_offline_cutoff(db: aiosqlite.Connection, now: str) -> str:
    """The `last_seen` below which a heartbeat status ages to offline.

    Reads `environment_offline_seconds` from settings rather than hardcoding the 90s default, so the
    sweep cannot silently disagree with the operator's configuration — a knob that is honoured in one
    place and ignored in another is its own defect class (see the container health-interval finding).
    The `max(30, ...)` floor matches the api_v2 call sites.
    """
    seconds = 90
    try:
        row = await (
            await db.execute("SELECT value FROM settings WHERE key = ?", ("environment_offline_seconds",))
        ).fetchone()
        if row and str(row[0] or "").strip():
            seconds = int(float(str(row[0]).strip()))
    except Exception:
        seconds = 90
    seconds = max(30, seconds)
    try:
        parsed = datetime.strptime(now, ISO_SECONDS)
    except Exception:  # pragma: no cover - `now` is produced by strftime above
        parsed = datetime.now(timezone.utc).replace(tzinfo=None)
    return (parsed - timedelta(seconds=seconds)).strftime(ISO_SECONDS)


async def _reconcile_terminal_controls(db: aiosqlite.Connection):
    # SAME format every other writer uses (`service.clock.now()`). isoformat() adds sub-second
    # precision, so `...:00.123456Z` sorts BEFORE `...:00Z` in any lexical comparison — and this
    # repo has already been bitten six times by exactly that (bughunt-round2-2026-07-03). Safe
    # today because the only comparison is datetime(handled_at), but one future `handled_at >= ?`
    # would be a silent bug. One shape, everywhere (C2).
    now = time.strftime(ISO_SECONDS, time.gmtime())
    # A queued `stop` is EXEMPT from the liveness sweep. This rule is implemented TWICE — here and
    # in `service/reconcilers/terminal_runs.py::_reconcile_ended_terminal_controls` — with the same
    # predicate and the same error
    # text, so BOTH must carry the exemption or neither does: an earlier fix landed in the reconciler
    # and changed nothing, because this copy still cancelled the stop.
    #
    # Why the exemption: stop_agent_worker marks the terminal 'stopping' (correct — the host has not
    # acknowledged) and queues the stop control in the same transaction. 'stopping' is not in the
    # active set below, and this sweep runs on a timer while the bridge polls every ~3s, so whenever
    # the sweep won the race it cancelled the very stop meant to kill the process. The PTY then
    # survived a "successful" Stop worker, and 900s later the stuck-stopping reaper wrote 'stopped'
    # over it — a row asserting a death that never happened. The pre-existing VIRTUAL path has the
    # identical exposure via 'stopped', which is why the fix is not "add 'stopping' to the set".
    # Killing a process is idempotent and stays desirable on a dead-looking row; server.js keeps an
    # orphan-pid fallback for the case where no bridge owns the PTY in memory any more.
    #
    # Accumulation is still bounded: the env-currency sweep immediately below fails controls whose
    # environment/bridge is no longer current, stop included, so a control for a dead environment
    # does not pile up forever.
    await db.execute(
        """
        UPDATE terminal_controls
        SET status = 'failed',
            handled_at = COALESCE(handled_at, ?),
            error = CASE WHEN COALESCE(error, '') = ''
                         THEN 'terminal is not active'
                         ELSE error END
        WHERE status IN ('pending', 'claimed')
          AND LOWER(COALESCE(action, '')) != 'stop'
          AND terminal_id IN (
              SELECT id FROM terminal_sessions
              WHERE status NOT IN ('starting', 'attached', 'running', 'active', 'idle')
          )
        """,
        (now,),
    )
    # A pending `stop` whose owning bridge restarted is RE-TARGETED at the environment's current
    # bridge instead of being cancelled. This closes the composed defect a reviewer identified on
    # `9747dda`, and the root cause is that it made existing machinery unreachable:
    #
    #   server.js carries an orphan-pid fallback for precisely "the owning bridge restarted/died and
    #   orphaned a still-live console" — it kills the persisted PTY root BY PID when a stop arrives
    #   at a bridge that never owned the terminal in memory. That fallback could never run, because
    #   this sweep failed the control the moment `bridge_id` stopped matching a current online
    #   environment. So the code written for bridge restart was dead in the exact scenario it names.
    #
    # Consequence when it fired: the PTY survived, and because stop_agent_worker writes the session
    # 'ended', Start was then free to spawn a SECOND worker for the same agent — the instance-leak
    # class this repo has been bitten by before. Re-targeting is the root fix; a Start gate would
    # only hide the duplicate, and a too-strict Start gate is what made the whole ef- team
    # unstartable in v0.1.
    #
    # Safe because a bridge on that environment is machine-local, so it can reap a local orphan, and
    # server.js still guards the pid (`orphanPidReapAllowed` refuses when the cmdline positively
    # names a DIFFERENT agent, and pidIsSelfProtected blocks bridge/shell/init).
    #
    # STOP-ONLY on purpose: replaying a queued keystroke at a different bridge would inject it into
    # whatever that bridge now owns. Only an idempotent kill may be re-pointed.
    #
    # The CLAIM MUST BE RELEASED TOO (review finding on `530ee71` — re-pointing alone was a no-op for
    # the commonest case). A bridge only ever claims PENDING work — the claim is
    # `SET status='claimed' ... WHERE id = ? AND status = 'pending'`. So a stop the dying bridge had
    # already claimed kept `status='claimed'`, got re-pointed at the new bridge, and the new bridge
    # never looked at it — stranded forever, which is precisely the state most likely to exist when a
    # bridge dies mid-stop. A claim held by a bridge that no longer exists is not a claim; drop it and
    # clear `claimed_at` so the replacement can take the work.
    #
    # Releasing is stop-only for the same reason re-targeting is: re-queueing a keystroke the previous
    # bridge may already have delivered would double-type it.
    actionable = _environment_actionable_sql()
    cutoff = await _environment_offline_cutoff(db, now)
    await db.execute(
        f"""
        UPDATE terminal_controls
        SET bridge_id = (
                SELECT COALESCE(environments.bridge_id, '')
                FROM environments
                WHERE environments.id = terminal_controls.environment_id
                  AND {actionable}
                LIMIT 1
            ),
            status = 'pending',
            claimed_at = NULL
        WHERE status IN ('pending', 'claimed')
          AND LOWER(COALESCE(action, '')) = 'stop'
          AND EXISTS (
              SELECT 1 FROM environments
              WHERE environments.id = terminal_controls.environment_id
                AND {actionable}
                AND COALESCE(environments.bridge_id, '') != COALESCE(terminal_controls.bridge_id, '')
                AND COALESCE(environments.bridge_id, '') != ''
          )
        """,
        (cutoff, cutoff),
    )
    # Everything still unreachable is failed, stop included — that is the bound on accumulation. A
    # stop whose environment has no bridge that can ACT (see _environment_actionable_sql: online or
    # degraded, and still heartbeating) cannot be delivered by anyone, so leaving it pending forever
    # would just grow the table. The re-target above has already rescued the cases a live bridge
    # could still reach.
    await db.execute(
        f"""
        UPDATE terminal_controls
        SET status = 'failed',
            handled_at = COALESCE(handled_at, ?),
            error = CASE
                WHEN COALESCE(error, '') != '' THEN error
                -- WHETHER ANYTHING EVER PICKED IT UP, because that is the first question asked of a
                -- control that did not run, and the two answers send an operator to different places.
                -- Measured on the live database: of 165 environment_controls carrying the old single
                -- message, 156 had NO `claimed_at` -- so the message named a claim-time check they
                -- never reached, and reads as "a bridge asked for this and was refused". The sibling
                -- drain in `superseded_bridge_stops.py` already distinguishes the two cases; this one
                -- said the same words for both.
                -- BOTH SIGNALS, because either one alone can be absent. `claimed_at` is the
                -- timestamp a claim writes; `status = 'claimed'` is the state it moves to. A row
                -- carrying one without the other is still a row something picked up, and calling it
                -- unclaimed would be the same kind of confident wrong answer this message replaces.
                WHEN COALESCE(claimed_at, '') = '' AND COALESCE(status, '') != 'claimed'
                    THEN 'environment bridge is no longer current; no bridge ever claimed this control'
                ELSE 'environment bridge is no longer current; claimed, then its bridge went away'
            END
        WHERE status IN ('pending', 'claimed')
          AND NOT EXISTS (
              SELECT 1 FROM environments
              WHERE environments.id = terminal_controls.environment_id
                AND COALESCE(environments.bridge_id, '') = COALESCE(terminal_controls.bridge_id, '')
                AND {actionable}
          )
        """,
        (now, cutoff),
    )
    # THE SAME RULE on the sibling table — same predicate, same error string. It gets the same
    # definition of "can act", because the answer to "is this environment's bridge reachable?" must
    # not depend on which table the control happens to live in. Fixing only `terminal_controls` would
    # have recreated, on purpose, the same-rule-two-answers defect this change exists to remove.
    await db.execute(
        f"""
        UPDATE environment_controls
        SET status = 'failed',
            handled_at = COALESCE(handled_at, ?),
            error = CASE
                WHEN COALESCE(error, '') != '' THEN error
                -- WHETHER ANYTHING EVER PICKED IT UP, because that is the first question asked of a
                -- control that did not run, and the two answers send an operator to different places.
                -- Measured on the live database: of 165 environment_controls carrying the old single
                -- message, 156 had NO `claimed_at` -- so the message named a claim-time check they
                -- never reached, and reads as "a bridge asked for this and was refused". The sibling
                -- drain in `superseded_bridge_stops.py` already distinguishes the two cases; this one
                -- said the same words for both.
                -- BOTH SIGNALS, because either one alone can be absent. `claimed_at` is the
                -- timestamp a claim writes; `status = 'claimed'` is the state it moves to. A row
                -- carrying one without the other is still a row something picked up, and calling it
                -- unclaimed would be the same kind of confident wrong answer this message replaces.
                WHEN COALESCE(claimed_at, '') = '' AND COALESCE(status, '') != 'claimed'
                    THEN 'environment bridge is no longer current; no bridge ever claimed this control'
                ELSE 'environment bridge is no longer current; claimed, then its bridge went away'
            END
        WHERE status IN ('pending', 'claimed')
          AND NOT EXISTS (
              SELECT 1 FROM environments
              WHERE environments.id = environment_controls.environment_id
                AND COALESCE(environments.bridge_id, '') = COALESCE(environment_controls.bridge_id, '')
                AND {actionable}
          )
        """,
        (now, cutoff),
    )
    await db.execute(
        """
        UPDATE terminal_controls AS stale
        SET status = 'failed',
            handled_at = COALESCE(handled_at, ?),
            error = CASE WHEN COALESCE(error, '') = ''
                         THEN 'superseded by newer pending resize'
                         ELSE error END
        WHERE stale.action = 'resize'
          AND stale.status = 'pending'
          AND EXISTS (
              SELECT 1 FROM terminal_controls newer
              WHERE newer.terminal_id = stale.terminal_id
                AND newer.action = 'resize'
                AND newer.status = 'pending'
                AND (
                    newer.requested_at > stale.requested_at
                    OR (newer.requested_at = stale.requested_at AND newer.id > stale.id)
                )
          )
        """,
        (now,),
    )
    await db.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_terminal_controls_pending_resize
        ON terminal_controls(terminal_id)
        WHERE action = 'resize' AND status = 'pending'
        """
    )
    columns = [
        row[2]
        for row in await (await db.execute(
            "PRAGMA index_info(idx_terminal_controls_env_status)"
        )).fetchall()
    ]
    if columns != ["environment_id", "bridge_id", "status", "requested_at", "id"]:
        await db.execute("DROP INDEX IF EXISTS idx_terminal_controls_env_status")
        await db.execute(
            """
            CREATE INDEX idx_terminal_controls_env_status
            ON terminal_controls(environment_id, bridge_id, status, requested_at, id)
            """
        )


async def _fail_pending_terminal_controls(
    db,
    terminal_id: str,
    *,
    handled_at: str,
    response_text: str,
    exclude_actions: tuple[str, ...] = (),
) -> int:
    """Fail this terminal's outstanding controls. `exclude_actions` spares specific actions.

    The exclusion exists for the liveness sweep, which must not cancel a queued `stop` (see
    _reconcile_ended_terminal_controls). It is NOT the default: the terminal-CLOSED callers below
    are right to fail everything, because once the process is genuinely gone a pending stop is moot.
    Needed as a parameter rather than relying on the caller's outer WHERE, since a terminal with
    BOTH an input and a stop outstanding is still selected by that query, and this helper would
    otherwise fail every pending row for it — taking the stop down with the input.
    """
    params: list[Any] = [terminal_id]
    exclusion_sql = ""
    normalized_exclusions = tuple(str(a or "").strip().lower() for a in exclude_actions if str(a or "").strip())
    if normalized_exclusions:
        placeholders = ", ".join("?" * len(normalized_exclusions))
        exclusion_sql = f" AND LOWER(COALESCE(action, '')) NOT IN ({placeholders})"
        params.extend(normalized_exclusions)
    cursor = await db.execute(
        f"""
        SELECT id
        FROM terminal_controls
        WHERE terminal_id = ?
          AND status IN ('pending', 'claimed')
          {exclusion_sql}
        """,
        tuple(params),
    )
    rows = await cursor.fetchall()
    control_ids = [str(row["id"] or "") for row in rows if str(row["id"] or "")]
    if not control_ids:
        return 0
    await db.executemany(
        """
        UPDATE terminal_controls
        SET status = 'failed',
            handled_at = COALESCE(handled_at, ?),
            error = CASE WHEN COALESCE(error, '') = '' THEN ? ELSE error END
        WHERE id = ?
        """,
        [(handled_at, response_text, control_id) for control_id in control_ids],
    )
    return len(control_ids)


async def _reconcile_ended_terminal_controls(db, *, limit: int = 500) -> int:
    """Fail controls nobody will ever run, so a caller is not left waiting on a dead terminal.

    A `stop` is EXEMPT (review finding on `35cc646`, a regression). `stop_agent_worker` marks the
    terminal `'stopping'` — correct, the host has not acknowledged — and queues the stop control in
    the SAME transaction. `'stopping'` is not in the active set below, and this sweep runs on a timer
    while the bridge polls every ~3s, so whenever the sweep won the race it cancelled the very stop
    that was supposed to kill the process. The PTY then survived a "successful" Stop worker, and
    900s later the stuck-stopping reaper wrote `'stopped'` over it — a row asserting a death that
    never happened. Strictly worse than the state lie it replaced, because the process lived.

    The pre-existing VIRTUAL path had the same exposure for a different reason: it marks `'stopped'`
    and queues its stop together, and `'stopped'` is not in the active set either. So the fix is not
    "add 'stopping' to the set" — it is that a stop must never be cancelled on liveness grounds.
    Killing a process is idempotent and stays desirable on a dead-looking row; server.js carries an
    orphan-pid fallback for exactly the case where no bridge owns the PTY in memory any more.

    Everything else still fails fast, which is the whole point of this reconcile — keystrokes into a
    console that is gone cannot be honoured, and the caller should learn that instead of hanging.
    """
    cursor = await db.execute(
        """
        SELECT DISTINCT terminal.id
        FROM terminal_sessions terminal
        JOIN terminal_controls control ON control.terminal_id = terminal.id
        WHERE terminal.status NOT IN ('starting', 'attached', 'running', 'active', 'idle')
          AND control.status IN ('pending', 'claimed')
          AND LOWER(COALESCE(control.action, '')) != 'stop'
        LIMIT ?
        """,
        (max(1, int(limit or 500)),),
    )
    total = 0
    now = _now()
    for row in await cursor.fetchall():
        total += await _fail_pending_terminal_controls(
            db,
            str(row["id"] or ""),
            handled_at=now,
            response_text="terminal is not active",
            exclude_actions=("stop",),
        )
    return total
