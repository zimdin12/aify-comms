"""Recording that an operator changed an agent's session mode.

Extracted from `switch_agent_session_mode` in `service/routers/agents/session_mode.py` in v0.5.4;
`test_switch_agent_session_mode_split_is_inert.py` inlines it back and AST-compares against the
pre-split fixture. The body is at its original 8-space column.

WHY IT WRITES A SYNTHETIC RUN, which is the part that reads as wrong until you know why. The audit
trail is `dispatch_events`, and its `run_id` is a NOT NULL foreign key to `dispatch_runs(id)` -- so
there is no way to attach an AGENT-level event to nothing. The workaround is an anchor row: a
`dispatch_runs` record with `dispatch_mode` and `execution_mode` both `audit`, born `completed` so
it can never enter the claim or queue paths, carrying a recognisable subject. The event then hangs
off it and operators see the switch in the same timeline as everything else, with no new table.

THE `completed` STATUS IS LOAD-BEARING. A row born queued or running would be claimable, and a
bridge would try to execute an audit record as work.
"""
from __future__ import annotations

import time
import uuid

from service.api_core.events import _append_dispatch_event


async def _record_session_mode_switch_audit(
    db, agent_id, current_mode, new_mode, effective_runtime, requested_by, now
) -> None:
        """Anchor a synthetic completed run, then attach the mode-switch event to it.

        Every argument is passed under the caller's own name: the extract-method gate splices this
        body back over its call without substituting arguments, so it refuses a call whose argument
        name differs from the parameter it fills.
        """
        # C1 audit log — `dispatch_events.run_id` is a NOT NULL FK to
        # `dispatch_runs(id)`, so we can't attach an agent-level event with
        # an empty run_id. Workaround: insert a synthetic anchor row into
        # `dispatch_runs` with status='completed' (so it never enters the
        # claim/queue paths) and a recognizable subject. Then attach the
        # mode_switch event to it. Operators see the audit row in the same
        # per-agent dispatch history view; no new table needed.
        event_type = f"mode_switch_{current_mode}_to_{new_mode}"
        audit_run_id = f"mode_switch_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        await db.execute(
            """
            INSERT INTO dispatch_runs (
                id, from_agent, target_agent, dispatch_mode, execution_mode,
                runtime, message_type, subject, body, status, summary, requested_at, finished_at
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                audit_run_id,
                requested_by,
                agent_id,
                "audit",
                "audit",
                effective_runtime,
                "audit",
                "session-mode-switch",
                f"agentId={agent_id} {current_mode}->{new_mode} by={requested_by}",
                "completed",
                event_type,
                now,
                now,
            ),
        )
        await _append_dispatch_event(
            db,
            audit_run_id,
            event_type,
            f"agentId={agent_id} by={requested_by}",
        )
