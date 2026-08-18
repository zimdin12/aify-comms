"""When a pending spawn request has stopped being a reason to wait.

REPORTED AS A DEADLOCK by sc-manager, 2026-08-18, with the timeline that makes it undeniable: a spawn
request sat `queued` for ~30 minutes, survived an operator bridge+wrapper restart, and the whole time
`comms_restart` — the exact action the backstop's own failure message prescribes — refused with

    HTTP 409: Agent "sc-architect" already has pending spawn request "spawn_…" (queued).

No worker, so the spawn stayed queued; a spawn pending, so the restart refused. From inside a session
there was no way out at all.

THE GUARD IS RIGHT TO EXIST. Two concurrent spawns for one agent is worse than one slow spawn: they
race for the same terminal and the loser is a leaked worker. The defect was that it was fail-safe in
ONE direction only — it protected against double-spawn at the cost of making a stuck spawn permanent.

A TTL RATHER THAN A `force` FLAG, which was the other option on the table. A flag requires a caller to
recognise the situation and choose correctly under pressure, and it will be passed by habit once
somebody hits the 409 twice. A TTL requires nobody to know anything and cannot be misused. The cost is
a window in which a genuinely slow spawn could be superseded, which is why the window is generous and
why PROGRESS resets it: a spawn that is doing anything at all updates its row.
"""

from __future__ import annotations

from service.clock import iso_to_epoch as _iso_to_epoch, now as _now

#: How long a pending spawn may show NO progress before a restart may supersede it.
#:
#: Well beyond the 180s undeliverable backstop that fails the dispatch, so the ordinary "target was
#: briefly busy" case never reaches this — by the time a spawn has been motionless this long, the
#: backstop has already declared the delivery dead and told the sender so.
ABANDONED_SPAWN_SECONDS = 600


def _spawn_request_is_abandoned(row, *, now: str | None = None,
                                seconds: int = ABANDONED_SPAWN_SECONDS) -> bool:
    """True when this pending spawn has made no progress for `seconds` and may be superseded.

    PROGRESS IS THE LATEST OF `updated_at`, `claimed_at` and `started_at`, not `created_at`. Keying on
    creation would supersede a spawn that is actively working through a slow start — exactly the
    healthy case — while a genuinely stuck one and a slow one would be indistinguishable. Every real
    step a spawn takes writes one of these three.

    A row with NO usable timestamp at all is NOT abandoned. That reads backwards at first, and it is
    deliberate: absent evidence is not evidence of absence, and the failure mode of guessing wrong
    here is a double spawn.
    """
    if row is None:
        return False
    reference = _iso_to_epoch(str(now or _now()))
    if not reference:
        return False
    latest = 0.0
    for field in ("updated_at", "claimed_at", "started_at", "created_at"):
        try:
            value = row[field]
        except (KeyError, IndexError, TypeError):
            continue
        stamp = _iso_to_epoch(str(value or "").strip()) if value else 0
        if stamp:
            latest = max(latest, stamp)
    if not latest:
        return False
    return (reference - latest) >= max(1, int(seconds))
