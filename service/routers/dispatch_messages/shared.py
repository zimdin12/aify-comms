"""Helpers owned by the dispatch+messages pair, and every borrow the pair still needs.

v0.5.2l. Two things live here, and the distinction is the whole point of the package:

OWNED (8 helpers). Used by dispatch handlers AND message handlers, and by nothing else.
Splitting dispatch and messages into separate modules would have made each of these a borrow in BOTH
— two shims, no owner, and a consolidation tag owed later. Moving the pair together lets them have a
real owner now. That is why the reviewer ruled for one combined tag.

BORROWED (33 names). Defined once here so `dispatch.py` and `messages.py` share one shim
rather than declaring their own. Each is still used by `agents`, by router-internal code, or by an
already-moved module borrowing it through the router — established by FOLLOWING THE SHIMS, not by raw
caller count. That distinction mattered: several names that looked local to this pair are borrowed by
channels, spawn_requests, sessions or the reconcilers, and moving them would have broken those.

Nearly all of it retires with `agents`, the last domain.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional


from service.api_core.runtime import _normalize_runtime
from service.api_core.serialization import (
    _dedupe_preserve,
)
from service.clock import now as _now
from service.db import get_db

# Resolved to their REAL owners, asked of the repo rather than guessed:
from service.reconcilers.dispatch_queue import _close_reconcilable_delivered_runs
# Imported for the ANNOTATION as much as the call: under postponed evaluation an unresolved
# model name does not fail import, it fails a type-hint gate or a request at runtime.
from service.models import DispatchClaimRequest

logger = logging.getLogger("aify_comms.routers.dispatch_messages.shared")








# _bridge_claim_block_reason moved to service/api_core/claim_gating.py in v0.5.4.


# _dispatch_conversation_context moved to service/api_core/claim_gating.py in v0.5.4.


# _dispatch_reply_pending moved to service/api_core/reply_contract.py in v0.5.4.




# _has_claimable_steerable_run moved to service/api_core/claim_gating.py in v0.5.4.


# _is_replaceable_auto_handoff_message moved to service/api_core/reply_linking.py in v0.5.4.














# _release_stale_console_owner_for_claim moved to service/api_core/claim_gating.py in v0.5.4.
















# _borrowed_unthreaded_handoff_window_ms moved to service/api_core/reply_linking.py in v0.5.4.








# Was a borrow shim: the owner lived in the control plane, which this module cannot import at
# module level without a cycle. It moved to service/api_core/dispatch_runs.py in v0.5.4.












# Was a borrow shim: the owner lived in the control plane, which a router cannot import at
# module level without a cycle. It moved to service/api_core/status_refresh.py in v0.5.4, so
# a plain import works.




# Was a borrow shim: the owner lived in the control plane, which this module cannot import at
# module level without a cycle. It moved to service/api_core/dispatch_sweeps.py in v0.5.4.


# Was a borrow shim: the owner lived in the control plane, which this module cannot import at
# module level without a cycle. It moved to service/api_core/dispatch_runs.py in v0.5.4.



# Was a borrow shim: the owner lived in the control plane, which this module cannot import at
# module level without a cycle. It moved to service/api_core/dispatch_sweeps.py in v0.5.4.






# _turn_busy_holds_delivery moved to service/api_core/claim_gating.py in v0.5.4.


# Was a borrow shim: the owner lived in the control plane, which a router cannot import at
# module level without a cycle. It moved to service/longpoll.py in v0.5.4 — the module that
# already owned the other waiter registry — so a plain import works.


# _console_dispatch_input_body moved to service/api_core/console_input_queue.py in v0.5.4.


# _dispatch_requires_reply moved to service/api_core/reply_expectation.py in v0.5.4.


# _link_reply_message_to_dispatch_run moved to service/api_core/reply_linking.py in v0.5.4.


# _message_type_expects_reply moved to service/api_core/reply_expectation.py in v0.5.4.


def _primary_result_message_id(message_id: str, recipients: list[str]) -> str:
    if len(recipients) == 1:
        return message_id
    if not recipients:
        return message_id
    return f"{message_id}-{recipients[0]}"


# _record_terminal_delivery_contract moved to service/api_core/console_input_queue.py in v0.5.4.


async def _resolve_recipient_ids(db, *, to: Optional[str], to_role: Optional[str], from_agent: str) -> list[str]:
    recipients: list[str] = []
    if to:
        recipients.append(to)
    if to_role:
        cursor = await db.execute("SELECT id FROM agents WHERE role = ? AND id != ?", (to_role, from_agent))
        recipients.extend([row["id"] for row in await cursor.fetchall()])
    return _dedupe_preserve(recipients)


async def _resolve_reply_parent_message_id(db, reply_id: Optional[str]) -> tuple[Optional[str], bool]:
    candidate = str(reply_id or "").strip()
    if not candidate:
        return None, True

    cursor = await db.execute("SELECT id FROM messages WHERE id = ? LIMIT 1", (candidate,))
    row = await cursor.fetchone()
    if row:
        return candidate, True

    cursor = await db.execute("SELECT message_id FROM dispatch_runs WHERE id = ? LIMIT 1", (candidate,))
    row = await cursor.fetchone()
    resolved = str((row["message_id"] if row else "") or "").strip()
    if resolved:
        return resolved, True

    return None, False


# _queue_console_dispatch_inputs moved to service/api_core/console_input_queue.py in v0.5.4.


# --- threading a reply that arrived without one --------------------------------------------------
#
# It arrived here from `messages.py` in v0.5.4 and left again in the same release, for
# `service/api_core/reply_linking.py`. The reason it stopped here on the way is worth keeping: it
# needed three names DECLARED in this module, and pushing it down would have meant importing them
# upward. What changed is that all three went with it — two were used by nothing else, and
# `_message_satisfies_reply_contract` already had an api_core owner. The obstacle was the cluster
# being split across layers, not the function's depth.

# _link_unthreaded_reply_to_recent_dispatch_run moved to service/api_core/reply_linking.py in v0.5.4.


# _queue_console_inputs_for_dispatch moved to service/api_core/console_input_queue.py in v0.5.4.
