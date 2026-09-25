"""What a reused `clientNonce` on /messages/send means: the same send again, or a mistake.

A nonce names ONE logical send, so a bridge that hit a transient socket error can retry it and get
the original messageId back instead of a duplicate (#240). A retry carries the same payload. A
DIFFERENT payload under a nonce already used is a caller bug, and answering it as a replay drops the
new send while reporting ok:true -- which is what happened until 0.7.0, because the lookup keyed on
(from_agent, client_nonce) alone. It is refused with 409 now, so the caller finds out.
"""

from __future__ import annotations

from typing import Optional

from fastapi import HTTPException


def _conflict(nonce: str, what: str) -> HTTPException:
    return HTTPException(
        409,
        f'clientNonce "{nonce}" was already used for a different message ({what}). '
        "A nonce names one logical send; use a fresh one for each new message.",
    )


async def prior_send_for_nonce(db, req, nonce: str) -> Optional[str]:
    """The messageId this nonce already produced, or None if it is unused.

    Raises 409 when the stored send differs from `req` in anything a retry would repeat verbatim:
    the type, subject or body, the named recipient, or whether it was a triggered dispatch.
    """
    rows = await (await db.execute(
        "SELECT id, to_agent, type, subject, body, dispatch_requested FROM messages "
        "WHERE from_agent = ? AND client_nonce = ? ORDER BY timestamp ASC",
        (req.from_agent, nonce),
    )).fetchall()
    if not rows:
        return None
    first = rows[0]
    for field, wanted in (("type", req.type), ("subject", req.subject), ("body", req.body)):
        if str(first[field] or "") != str(wanted or ""):
            raise _conflict(nonce, f"another {field}")
    if req.to and req.to not in {row["to_agent"] for row in rows}:
        raise _conflict(nonce, "another recipient")
    triggered = int(bool(req.trigger))
    if any(int(row["dispatch_requested"] or 0) != triggered for row in rows if row["to_agent"] != "dashboard"):
        raise _conflict(nonce, "a triggered send" if req.trigger else "an untriggered send")
    return first["id"]
