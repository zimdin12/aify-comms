"""Whether a host is still pushing its agent definitions (P0 C11, arm 3).

A host that stops pushing (its aify-env downgraded, or a new claimer that never pushes) leaves its rows
governing as last pushed. That is by design, and it must be visible: after STALE_AFTER_SECONDS with no
push, the environment says "definitions from <machine> not refreshed since <time>", and the dashboard and
`aify-comms doctor` both read it from here.

`pushed_at` is stamped by every accepted push, replays included. `updated_at` moves only when the
snapshot changes, so it is the age of the last CHANGE: read as the age of the last push, every idle host
would go stale ten minutes after its last edit.
"""
from __future__ import annotations

from typing import Any

from service.clock import iso_to_epoch

#: Ten pushes missed: aify-env pushes every 60 s (definition-sync.mjs PUSH_INTERVAL_MS).
STALE_AFTER_SECONDS = 600


def definition_freshness(store: Any, now: str) -> dict[str, Any]:
    """PURE. One `definition_stores` row and the service's clock in, what the environment carries out.

    A row from before `pushed_at` existed falls back to `updated_at`, never later than its last push, so
    it can only warn early, and only until that host's next push. An unreadable time is stale: a guard
    that passes on a missing input is decoration."""
    pushed = str((store["pushed_at"] if "pushed_at" in store.keys() else None) or store["updated_at"] or "")
    pushed_epoch = iso_to_epoch(pushed, default=None)
    now_epoch = iso_to_epoch(now, default=None)
    stale = pushed_epoch is None or now_epoch is None or now_epoch - pushed_epoch > STALE_AFTER_SECONDS
    since = (pushed or "unknown") if stale else None
    return {
        "machineId": store["machine_id"],
        "storeId": store["store_id"],
        "revision": store["revision"],
        "pushedAt": pushed,
        "notRefreshedSince": since,
        # THE SENTENCE IS WRITTEN HERE, ONCE: the dashboard and the doctor print it as given.
        "notice": f"definitions from {store['machine_id']} not refreshed since {since}" if stale else "",
    }
