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


#: HOW FRESH A BRIDGE MUST BE TO CLAIM A SPAWN. One window, both arms.
#:
#: The two arms disagreed. A STAMPED row aged against the environment's own `offline_seconds`
#: default of 90; an ABSENT one was resolved against `bridge_instances` using
#: `ACTIVE_RUN_BRIDGE_STALE_SECONDS`, which is 120. So the SAME bridge at age 100s was live before
#: it gained a stamp and dead after -- a migration flipping a liveness answer with nothing about the
#: bridge having changed. Two numbers for one question is the shape that produced the original
#: strand: `last_seen` and `bridgeLastSeen` answering "is there a claimer" differently.
#:
#: Named here rather than borrowed from either side, so neither can drift without the other.
SPAWN_CLAIMER_FRESH_SECONDS = 90

#: What `metadata.bridgeLastSeen` says, as four DISTINGUISHABLE answers rather than one boolean.
#:
#: The boolean collapsed two of them and got the collapse backwards. It read ABSENT as "unknown, and
#: unknown means yes", so an environment that never had a bridge stamp -- which is every row
#: registered before the field existed -- was treated as having a live one for ever. An aify-env
#: advertisement keeps such a row `online` indefinitely with nothing able to claim a spawn, which is
#: the queued-for-ever strand the gate was added to prevent, reintroduced through the gate itself.
#: It also read UNPARSEABLE as absent, so invalid data became authorization.
BRIDGE_STAMP_FRESH = "fresh"
BRIDGE_STAMP_STALE = "stale"
BRIDGE_STAMP_ABSENT = "absent"
BRIDGE_STAMP_INVALID = "invalid"


def bridge_stamp_state(environment, *, offline_seconds: int = SPAWN_CLAIMER_FRESH_SECONDS) -> str:
    """Classify `metadata.bridgeLastSeen`. PURE -- the caller resolves ABSENT against the authority.

    ABSENT IS NOT AN ANSWER, it is a question the caller has to take elsewhere. `bridge_instances` is
    the authority on whether a bridge is alive for an environment, and it is the same table the turn
    lease consults; asking it turns "we have no stamp" into evidence rather than into a guess, which
    is why there is no timed grace here to expire and no doctor row for one.

    INVALID IS NEVER LIVE. A malformed stamp is corrupt data, not a heartbeat, and the previous
    reading -- "unparseable is the same as absent" -- let a bad write authorize a spawn.
    """
    metadata = environment.get("metadata") if isinstance(environment, dict) else None
    stamp = str((metadata or {}).get("bridgeLastSeen") or "").strip()
    if not stamp:
        return BRIDGE_STAMP_ABSENT
    try:
        last = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except Exception:
        return BRIDGE_STAMP_INVALID
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    window = timedelta(seconds=max(15, int(offline_seconds or 90)))
    age = datetime.now(timezone.utc) - last
    # A FUTURE stamp is not fresh. `age <= window` is satisfied for ever by one hours ahead, so a
    # clock-skewed or forged write would read live permanently -- the same asymmetry the turn ceiling
    # had to close. Ordinary skew is tolerated; a stamp beyond it is corrupt, not early.
    if age < -timedelta(seconds=BRIDGE_STAMP_SKEW_TOLERANCE_SECONDS):
        return BRIDGE_STAMP_INVALID
    return BRIDGE_STAMP_FRESH if age <= window else BRIDGE_STAMP_STALE


#: How far ahead of us a bridge stamp may be and still count. NOT ZERO: `aify-comms doctor` once
#: called every environment dead because the container clock ran 4.1s ahead of the host, and a
#: strict no-future rule reproduces exactly that. Two minutes separates skew from a bogus stamp.
BRIDGE_STAMP_SKEW_TOLERANCE_SECONDS = 120
def environment_has_live_bridge(
    environment, *, offline_seconds: int = SPAWN_CLAIMER_FRESH_SECONDS,
    bridge_rows_say_live: bool | None = None,
) -> bool:
    """Has a BRIDGE spoken for this environment recently?

    A DIFFERENT QUESTION FROM `environment_effective_status`, and they were the same one until
    2026-08-30. That function ages on `last_seen`, which only a bridge used to write; aify-env now
    heartbeats the same row to describe the host, so a fresh `last_seen` no longer implies anything
    can start a process here. A spawn was accepted against exactly that and queued for ever.

    `bridge_rows_say_live` IS THE AUTHORITY'S ANSWER for a row with no stamp, supplied by the caller
    so this stays pure. `None` means the caller did not ask -- and not asking is not evidence, so an
    unstamped row without an answer is NOT live. That direction is the whole correction: the previous
    version returned True there, which made "we never checked" indistinguishable from "yes".
    """
    state = bridge_stamp_state(environment, offline_seconds=offline_seconds)
    if state == BRIDGE_STAMP_FRESH:
        return True
    if state == BRIDGE_STAMP_ABSENT:
        return bridge_rows_say_live is True
    # STALE and INVALID are both no. Stale is a bridge that stopped; invalid is data that cannot
    # support a claim. Neither is a live claimer, and neither may authorize a spawn.
    return False


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
