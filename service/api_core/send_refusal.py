"""Refusing a send because a recipient cannot start live work, and reporting why per recipient.

DEDUPLICATED out of `send_message` in v0.5.4. The block appeared TWICE in that one function, thirty
lines apart and byte-identical: once after the trigger preflight and once after the launch pass, each
answering the same question with the same words. Two copies of a refusal is how the two paths quietly
start disagreeing about what a caller is told.

WHY IT WAS NOT MERGED EARLIER. The extract-method gate refused any helper with more than one call
site — "inline-back is only defined for a single call site" — which ruled out the one operation an
extraction exists for when a block appears twice. v0.5.4 made inline-back splice the body back into
EVERY site, so the round trip now proves this shape, and refuses it if the original had DIFFERENT
code at the two places.

IT DOES NOT SHORT-CIRCUIT THE REPLY PATH, and that asymmetry is the important part. Both call sites
sit under `if not_started and not is_reply:` — a REPLY is never hard-rejected here, it falls through
to persist and thread. That condition stays at the call sites rather than moving inside, because it
is the caller's policy about which sends may be refused, not part of building the refusal.

DB ACCESS: `db` is passed in, the commit is the caller's own transaction being closed before the
refusal is returned, and no connection is opened here.
"""
from __future__ import annotations

from service.api_core.status_refresh import _get_recipient_info


async def _refuse_send_to_unstartable_recipients(db, recipients, not_started, warnings):
                """The refusal payload, with each recipient's current status attached."""
                recipient_info = {}
                for r in recipients:
                    info = await _get_recipient_info(db, r)
                    if info:
                        recipient_info[r] = {
                            "status": info["status"],
                            "unread": info["unread"],
                            "runtime": info["runtime"],
                            "machineId": info["machineId"],
                        }
                await db.commit()
                return {
                    "ok": False,
                    "error": "Message was not sent because one or more recipients cannot start live work now.",
                    "recipients": recipients,
                    "recipientStatus": recipient_info,
                    "dispatchRuns": [],
                    "notStarted": not_started,
                    "consoleDeliveries": [],
                    "warnings": warnings,
                }
