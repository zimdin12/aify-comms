"""Threading an outgoing message back onto the run it answers.

Extracted from `send_message` in `service/routers/dispatch_messages/messages.py` in v0.5.4;
`test_send_message_reply_threading_split_is_inert.py` inlines it back and AST-compares against the
pre-split fixture. The body is at its original 8-space column.

IT WAS BLOCKED FOR TWO SLICES, and the sequence is the point. Both writers it calls lived in
`service/routers/dispatch_messages/shared.py`, and an api_core leaf importing from `service.routers`
is the cycle the layering exists to prevent -- so this block could not move until they did. They
left for `service/api_core/reply_linking.py` earlier in the same release, and this is what that
unblocked. Fourth and last instance in v0.5.4 of a router-declared helper standing in the way.

TWO PATHS, AND THE SECOND ONE GUESSES. If the sender said what it was replying to, the run is known
and is closed directly. If it did not, there is no thread to follow, so each recipient is matched
against the most recent run from this sender within a bounded window. Guessing wrong closes a run
that is still owed an answer, which is why the window exists and why the two paths are exclusive.

THE PER-RECIPIENT ID IS NOT COSMETIC. A fan-out gives each recipient its own suffixed message id,
because one message id cannot thread N conversations; a single-recipient send keeps the bare id so
the reply threads against the id the sender was handed.
"""
from __future__ import annotations

from service.api_core.reply_linking import (
    _link_reply_message_to_dispatch_run,
    _link_unthreaded_reply_to_recent_dispatch_run,
)


async def _thread_reply_onto_dispatch_runs(
    db, req, recipients, msg_id, ts, resolved_in_reply_to, linked_result_message_id,
) -> None:
        """Close the run this message answers, or find the one it most likely answers.

        Every argument is passed under the caller's own name: the extract-method gate splices this
        body back over its call without substituting arguments, so it refuses a call whose argument
        name differs from the parameter it fills.
        """
        if resolved_in_reply_to:
            await _link_reply_message_to_dispatch_run(
                db,
                from_agent=req.from_agent,
                resolved_in_reply_to=resolved_in_reply_to,
                reply_message_id=linked_result_message_id,
                reply_type=req.type,
                reply_body=req.body,
            )
        else:
            for r in recipients:
                recipient_message_id = f"{msg_id}-{r}" if len(recipients) > 1 else msg_id
                await _link_unthreaded_reply_to_recent_dispatch_run(
                    db,
                    from_agent=req.from_agent,
                    to_agent=r,
                    reply_message_id=recipient_message_id,
                    reply_type=req.type,
                    reply_subject=req.subject,
                    reply_body=req.body,
                    reply_timestamp_ms=ts,
                )
