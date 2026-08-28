"""Telling a superseded environment bridge to stop, without letting the request pile up.

Extracted from `environment_heartbeat` in `service/routers/environments.py` in v0.5.4;
`test_environment_heartbeat_split_is_inert.py` inlines it back and AST-compares against the pre-split
fixture. The body is at its original 8-space column so the SQL literals are preserved byte-for-byte.

WHEN A NEW BRIDGE TAKES OVER AN ENVIRONMENT the old one must be told to stop, and the way it is told
is a pending row in `environment_controls` that the old bridge claims on its ~3s poll. That works
while the old bridge is alive. When it is not -- the usual case, since it was superseded because it
died -- the row is never claimed and nothing ever removes it.

THE ACCUMULATION IS THE DEFECT (2026-07-03): one stop per restart, ninety-nine observed for a single
environment. So each heartbeat first DRAINS the stops that have been pending well past the point a
live bridge would have claimed them, and only then queues a new one -- and only if this bridge has no
stop pending or claimed already.

THE DRAIN IS NOT A SAFETY MECHANISM and must not be read as one. The claim-side guard is what
prevents a stale stop from killing a live bridge; this only keeps the table bounded. Widening the
TTL here cannot make a stop dangerous, and narrowing it cannot make one safe.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from service.clock import ISO_SECONDS

# A superseded env bridge polls env-control every ~3s, so it claims its stop
# within seconds. A `server:superseded-bridge` stop still pending well past this
# targets a bridge that never came back — drained on the next registration to
# keep environment_controls from growing one-row-per-restart (see the 2026-07-03
# accumulation that self-terminated fresh bridges).
#
# Moved here WITH the block in v0.5.4: it had exactly one reader, and a constant whose only use
# is in another module is a fork waiting to happen.
SUPERSEDE_STOP_STALE_SECONDS = 300


async def _queue_stop_for_superseded_bridge(db, env_id, superseded_bridge_id, req, now) -> None:
        """Drain the stale stops for this environment, then queue one for the superseded bridge.

        Guarded inside rather than at the call site, which is what the block looked like before it
        moved -- the extract-method gate splices this body back over its call verbatim, so hoisting
        the condition would break the round trip that proves the move changed nothing. Every argument
        is passed under the caller's own name for the same reason: inline-back does not substitute
        arguments.
        """
        if superseded_bridge_id:
            # Bound accumulation: drain superseded-bridge stops for this env that have
            # been pending well past the point a live superseded bridge would have
            # claimed them (it polls every ~3s). Anything still pending after the TTL
            # targets a bridge that never came back; left unbounded these accumulate
            # one-per-restart (99 observed for a single env, 2026-07-03). The claim-side
            # guard already prevents any of them from stopping a live bridge; this just
            # keeps the table from growing without limit.
            drain_cutoff = (
                datetime.now(timezone.utc) - timedelta(seconds=SUPERSEDE_STOP_STALE_SECONDS)
            ).strftime(ISO_SECONDS)
            await db.execute(
                """
                UPDATE environment_controls
                SET status = 'failed',
                    handled_at = ?,
                    error = 'stale superseded-bridge stop drained (target bridge never claimed)'
                WHERE environment_id = ?
                  AND action = 'stop'
                  AND status = 'pending'
                  AND requested_by = 'server:superseded-bridge'
                  AND requested_at < ?
                """,
                (now, env_id, drain_cutoff),
            )
            pending_cursor = await db.execute(
                """
                SELECT id
                FROM environment_controls
                WHERE environment_id = ?
                  AND bridge_id = ?
                  AND action = 'stop'
                  AND status IN ('pending', 'claimed')
                LIMIT 1
                """,
                (env_id, superseded_bridge_id),
            )
            pending = await pending_cursor.fetchone()
            if not pending:
                await db.execute(
                    """
                    INSERT INTO environment_controls (
                        id, environment_id, bridge_id, machine_id, action, status, requested_by, requested_at
                    ) VALUES (?,?,?,?,?,?,?,?)
                    """,
                    (
                        f"envctl-{uuid.uuid4().hex}",
                        env_id,
                        superseded_bridge_id,
                        req.machineId or "",
                        "stop",
                        "pending",
                        "server:superseded-bridge",
                        now,
                    ),
                )
