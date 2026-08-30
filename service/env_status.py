"""Environment liveness, derived — not a stored status column.

v0.5 slice 2. `environment_effective_status` moved out of `service/routers/api_v2.py` so the spawn
reconcilers can use it without importing the router back (the cycle this release exists to remove).
A leaf module: it imports nothing from the service.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

# EVERY status an `environments.status` column can hold. It had no owner until 2026-08-16: the
# vocabulary lived in the docstring below ("only ever written `online|degraded|offline` by a
# registration, plus `forgotten`/`disabled` server-side"), in three scattered write sites, and — in
# full — only in JavaScript, as `ENV_KNOWN_STATES` in `mcp/stdio/doctor-predicates.js`, which reports
# any status outside it as unrecognised. Prose is not read by the suite, so the only complete
# statement of this vocabulary was one a Python change could not break.
#
# Ordered narrowest-first, and each set is a strict subset of the next:
#
#   heartbeat  {online, degraded}                    -> a bridge is talking to us; ages to offline
#   registrable  + {offline}                         -> what a REGISTERING bridge may ask to be
#   all          + {forgotten, disabled}             -> plus the two an operator action writes
#
# `forgotten` (the forget endpoint) and `disabled` (the disable control) are DECISIONS, not
# observations, which is why a bridge cannot request them and why the derivation below returns them
# untouched. `_environments` also carries a `status_rank` map listing a sixth spelling, `unknown`;
# nothing can write it and its `.get(..., 5)` default already covers anything unranked, so it is a
# dead entry rather than a member of this vocabulary.
ENVIRONMENT_STATUSES = frozenset({"online", "degraded", "offline", "forgotten", "disabled"})
ENVIRONMENT_REGISTRABLE_STATUSES = frozenset({"online", "degraded", "offline"})

# Which stored statuses mean "this environment has been heartbeating". Moved with the function that
# reads it, in slice 2 — a constant is a dependency exactly as much as a function call is, and my
# dependency scan only counted CALLS. That is why this move needed three follow-up fixes.
_ENVIRONMENT_HEARTBEAT_STATUSES = {"online", "degraded"}


def environment_has_live_bridge(environment, *, offline_seconds: int = 90) -> bool:
    """Has a BRIDGE spoken for this environment recently?

    A DIFFERENT QUESTION FROM `environment_effective_status`, and they were the same one until
    2026-08-30. That function ages on `last_seen`, which only a bridge used to write; aify-env now
    heartbeats the same row to describe the host, so a fresh `last_seen` no longer implies anything
    can start a process here. A spawn was accepted against exactly that and queued for ever.

    Reads `metadata.bridgeLastSeen`, which is written only by a beat carrying a `bridgeId` and
    preserved -- never refreshed -- by one that is not.

    ABSENT MEANS UNKNOWN, AND UNKNOWN MEANS YES. Every environment registered before this field
    existed has no `bridgeLastSeen`, and reading that as "no bridge" would refuse every spawn on every
    host until each one's bridge restarted. A missing field is not evidence of absence; the freshness
    check applies only once there is something to check.

    @param environment  an `_environment_record_to_dict` result
    """
    metadata = environment.get("metadata") if isinstance(environment, dict) else None
    stamp = str((metadata or {}).get("bridgeLastSeen") or "").strip()
    if not stamp:
        return True
    try:
        last = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except Exception:
        # Unparseable is the same as absent: it is not evidence that no bridge is there.
        return True
    return datetime.now(timezone.utc) - last <= timedelta(seconds=max(15, int(offline_seconds or 90)))


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


def live_environment_bridge_ids(rows, *, offline_seconds: int = 90) -> set[str]:
    """The `bridge_id` of every environment that is ONLINE right now.

    PURE, and it takes rows rather than a connection on purpose: the query is trivial and differs by
    caller, while the part that can be WRONG -- which derived status counts as live -- is one answer
    that two callers must not hold separately.

    Both readers use it to decide who has authority. `spawn_lifecycle` fails a `running` spawn whose
    claiming bridge is not in this set; `PATCH /agents/{id}/runtime-state` lets an id in this set take
    ownership of a managed agent and refuses one that is not. `degraded` is deliberately NOT live here:
    it means the bridge is answering but unhealthy, which is enough to keep its own work and not enough
    to be handed somebody else's.
    """
    live = set()
    for row in rows or []:
        bridge_id = str(row["bridge_id"] or "").strip()
        if not bridge_id:
            continue
        if environment_effective_status(row, offline_seconds=offline_seconds) == "online":
            live.add(bridge_id)
    return live
