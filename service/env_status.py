"""Environment liveness, derived — not a stored status column.

v0.5 slice 2. `environment_effective_status` moved out of `service/routers/api_v2.py` so the spawn
reconcilers can use it without importing the router back (the cycle this release exists to remove).
A leaf module: it imports nothing from the service.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

# Which stored statuses mean "this environment has been heartbeating". Moved with the function that
# reads it, in slice 2 — a constant is a dependency exactly as much as a function call is, and my
# dependency scan only counted CALLS. That is why this move needed three follow-up fixes.
_ENVIRONMENT_HEARTBEAT_STATUSES = {"online", "degraded"}


def environment_effective_status(row, *, offline_seconds: int = 90) -> str:
    """Derive an environment's status, ageing a silent bridge to `offline`.

    The stored column is only ever written `online|degraded|offline` by a registration, plus
    `forgotten`/`disabled` server-side — nothing ages it, so the derivation here IS the liveness
    truth every caller depends on.

    BUG (fixed 2026-07-26, found in review): the staleness check was gated on `status == "online"`,
    so a `degraded` environment NEVER aged out. It stayed "degraded" forever after the bridge died,
    and because callers treat degraded as still-connected that resurrected the exact false-green
    class `aify-doctor`'s env-bridge check exists to prevent — a dead bridge reported as live.
    Terminal states (`offline`/`forgotten`/`disabled`) are returned untouched: they are decisions,
    not observations, and must not be overridden by a timestamp.
    """
    status = str(row["status"] or "online")
    if status in _ENVIRONMENT_HEARTBEAT_STATUSES:
        try:
            last = datetime.fromisoformat(str(row["last_seen"] or "").replace("Z", "+00:00"))
            if datetime.now(timezone.utc) - last > timedelta(seconds=max(15, int(offline_seconds or 90))):
                status = "offline"
        except Exception:
            pass
    return status
