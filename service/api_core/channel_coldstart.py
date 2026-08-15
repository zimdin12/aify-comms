"""Send-time coldstart for COLD managed channel members.

Extracted from `send_channel_message` (`service/routers/channels.py`) in v0.5.4. That handler was 184
lines; this is the self-contained part that reaches OUTSIDE the message write to wake workers.

WHY IT EXISTS AT ALL, kept with the code because the reason is an incident. Channel posts used to
create queued runs and rely entirely on the 180s queued-run backstop to spawn workers — and before
that backstop had a coldstart rescue, those runs simply FAILED. That is the "sc-manager's broadcasts
left targets available, no answers" report (#191). This mirrors the direct-send path: spawn a
managed-warm worker NOW for each launchable member with no live wrapper child, so a channel roll-call
wakes a cold team in seconds rather than minutes.

IDEMPOTENT BY DESIGN: `_coldstart_spawn_request_for_dispatch` short-circuits on a pending or booting
spawn request, and an unresolvable environment returns False, leaving the run queued for the backstop
rescue exactly as before.

DB ACCESS: `db` is passed in, nothing opens a connection or commits — this joins its caller's
transaction.
"""
from __future__ import annotations

from service.api_core.channel_delivery import _CHANNEL_CLAIM_RUNTIMES
from service.api_core.liveness import _has_live_managed_wrapper_child
from service.api_core.runtime import _normalize_runtime, _normalize_session_mode
from service.api_core.settings import _load_settings
from service.api_core.dispatch_start import _coldstart_spawn_request_for_dispatch


async def _coldstart_cold_channel_members(db, req, launchable_recipients):
            """Spawn a managed-warm worker for each launchable member that has no live one."""
            # Send-time coldstart for COLD managed members (2026-07-02). Channel posts
            # previously created queued runs and relied entirely on the 180s queued-run
            # backstop to spawn workers (and before the backstop's coldstart-rescue existed,
            # those runs just FAILED — the "sc-manager's broadcasts left targets available,
            # no answers" incident, #191). Mirror the direct-send path: spawn a managed-warm
            # worker NOW for each launchable member with no live wrapper child, so a channel
            # roll-call wakes a cold team in seconds, not minutes. The helper is idempotent
            # (pending/booting spawn_request short-circuits; unresolvable env returns False,
            # leaving the run queued for the backstop rescue as before).
            coldstart_settings = await _load_settings(db)
            for recipient_id, _exec_mode in launchable_recipients:
                agent_cursor = await db.execute("SELECT * FROM agents WHERE id = ?", (recipient_id,))
                agent_row = await agent_cursor.fetchone()
                if not agent_row:
                    continue
                if _normalize_session_mode(agent_row["session_mode"] or "resident") != "managed":
                    continue
                member_runtime = _normalize_runtime(agent_row["runtime"] or "")
                # Wrapper-child rows only exist for the channel-claim runtimes; for
                # pi/opencode (native RPC controllers inside the env bridge) the gate
                # below is permanently False, so coldstarting on it would duplicate-spawn
                # a LIVE worker on every channel post. Those runtimes spawn on claim,
                # same as the direct-send path.
                if member_runtime not in _CHANNEL_CLAIM_RUNTIMES:
                    continue
                if await _has_live_managed_wrapper_child(db, recipient_id):
                    continue
                await _coldstart_spawn_request_for_dispatch(
                    db,
                    recipient_id,
                    runtime=member_runtime,
                    settings=coldstart_settings,
                    requested_by=req.from_agent,
                )
