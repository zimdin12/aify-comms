"""Telling a connected dashboard that a host's definitions went stale (P0 C11, arm 3).

Staleness is a function of the clock. Nothing is written when a host stops pushing, so the change feed,
which reports commits, says nothing, and a dashboard holding its socket never refetches the environments
that would show it: comms-senior-dev's review of cbcc26d0 reproduced a card still fresh at +601 s, and an
hour later, with zero fetches. Each sweep pass asks which stores are stale and, when that set moves,
reports `definition_stores` as changed, the way `status_cache` reports an agent whose lease ran out.

WHEN, honestly: at the first successful sweep after the deadline. That is not a 60 s guarantee end to
end: the loop sleeps 60 s AFTER each completed sweep, and the sweep's own work, the feed's coalescing and
the client's debounce and fetch all add to it (comms-senior-dev's review of c8692f10).

A FIRST PASS COMPARES AGAINST NOTHING STALE, so a host already stale at the first successful sweep after a
start is reported. It once compared against nothing at all and said nothing, and a client that had read
the row fresh just before the deadline then never heard of it (same review). The price is one refetch per
start while some host is stale.
"""
from __future__ import annotations

from typing import Optional

from service.api_core.definition_freshness import definition_freshness
from service.change_feed import CHANGE_FEED


class DefinitionStaleness:
    """Which machines' definitions were stale at the last pass. One per process, like the feed it serves.
    Before any pass, none are: a first pass that finds one stale reports it."""

    def __init__(self) -> None:
        self._stale: Optional[frozenset] = None

    def moved(self, stale: frozenset) -> bool:
        """Record this pass's stale set; true when it differs from the last pass's."""
        previous, self._stale = self._stale or frozenset(), frozenset(stale)
        return previous != self._stale


TRACKER = DefinitionStaleness()


async def report_definition_staleness(db, now: str, *, tracker: Optional[DefinitionStaleness] = None, feed=None) -> int:
    """1 when the set of stale stores moved since the last pass (and the feed was told), else 0. Reads only.
    The tracker and the feed are looked up at call time, so a test can hand in its own."""
    tracker = tracker if tracker is not None else TRACKER
    feed = feed if feed is not None else CHANGE_FEED
    rows = await (await db.execute(
        "SELECT machine_id, store_id, revision, environment_id, updated_at, pushed_at FROM definition_stores")).fetchall()
    stale = frozenset(row["machine_id"] for row in rows if definition_freshness(row, now)["notRefreshedSince"])
    if not tracker.moved(stale):
        return 0
    feed.derived_moved("definition_stores")
    return 1
