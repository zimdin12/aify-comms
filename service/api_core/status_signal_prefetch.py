"""Per-agent status signals, read one at a time or prefetched for a whole fleet.

MEASURED 2026-08-27 by counting `aiosqlite.Connection.execute` calls, not by timing anything -- the
same code on this host timed 44-47ms and then 22-25ms minutes later, because the live fleet is the
load. Round-trips are deterministic and attributable to a call site.

    `_refresh_expired_agent_live_states`, unbounded (the reconcile sweep's call):
        1 agent -> 9 round-trips, 5 -> 37, 20 -> 142, 40 -> 282.  Exactly 7.0 per added agent.

    Share of ONE whole reconcile pass that is this per-agent refresh:
        5 agents 31%,  20 agents 54%,  40 agents 61%.

The share GROWS with fleet size, so on a real fleet it is the dominant term rather than a rounding
error inside a bigger cost. That is what makes it worth touching a path this safety-sensitive; a raw
count on its own would not have.

RE-MEASURED 2026-08-29 WITH THE SAME METHOD, and the figures above did not survive: 5 agents -> 54
round-trips, 20 -> 204, 40 -> 404. Exactly 10.0 per added agent, where this docstring records 5.0 two
days earlier. The 2026-08-27 numbers are left as written rather than corrected, because they were
true of the code that day and rewriting them would hide the point: NOTHING WAS WATCHING THE NUMBER,
so it moved. `service/tests/test_the_live_state_refresh_holds_its_per_agent_cost.py` now holds it at
a ceiling that fails in both directions -- above it, because a per-agent read costs one round-trip
per agent on every sweep pass forever; below it, because slack left above the real cost is how 5
became 10 with nobody noticing.

`GET /agents` is NOT the problem and is not changed: it caps the recompute per request, so it is flat
at 65 round-trips whether the fleet is 20 agents or 40. The unbounded sweep pays the full price.

WHAT THIS DOES. The per-agent reads that are plain single-row lookups keyed on agent_id with no
filtering, grouping or ordering become one query each for the whole batch. `agent_status_state` and
`agent_console_signal` came first (7.0 per added agent -> 5.0); `agent_turn_state` joined them on
2026-08-29, taking the absolute per-agent cost of a refresh from 8 round-trips to 7.

WHAT IS DELIBERATELY LEFT ALONE. The remaining per-agent reads are not lookups: the two dispatch_runs
reads, the agent_sessions read and both terminal_sessions reads filter, order or aggregate, and the
channel-sidecar `bridge_instances` read takes a MAX. Each can be batched, and each needs a GROUP BY
whose equivalence to the single-row form is its own argument. Doing one of those carelessly in the
status path costs more than the round-trips are worth.

WHY AN OBJECT AND NOT A DICT ARGUMENT. The caller should not branch on whether a prefetch happened;
`signals.status_state(db, aid)` is one line either way, and the fallback reads the same row the
inline query always read. So every existing caller is byte-for-byte unaffected -- the default is the
live reader, and only the batch path passes a prefetched one.

THE SNAPSHOT IS DELIBERATE. Prefetching reads all agents at one moment instead of each at its own.
For a sweep that runs every 60s and feeds a TTL'd cache, a few milliseconds of skew is immaterial, and
a consistent snapshot across the fleet is the better of the two -- the per-agent version smears the
fleet's state across the length of the loop.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional

#: SQLite's default host-parameter ceiling is 999. Chunking keeps a large fleet from ever reaching it,
#: and the chunk size is stated rather than assumed so the limit is visible to the next reader.
_MAX_PARAMS_PER_QUERY = 400

#: `turn_started_at` IS SELECTED HERE, and the omission was not harmless. The in_turn clamp reads
#: the anchor through `_turn_anchor`, which falls back to `last_event_at` when the anchor is absent
#: -- a fallback meant for rows written before the column existed. A query that simply did not ASK
#: for the column hit that same fallback, so the whole anchoring fix was inert on the prefetched
#: path while every targeted test passed. `test_every_status_state_read_asks_for_the_anchor.py`
#: derives this requirement rather than trusting the next person to remember it.
_STATUS_STATE_SQL = ("SELECT agent_id, in_turn, awaiting_input, last_event_at, turn_started_at "
                     "FROM agent_status_state WHERE agent_id IN ({})")
_CONSOLE_SIGNAL_SQL = "SELECT agent_id, working_at, subagents_at FROM agent_console_signal WHERE agent_id IN ({})"
#: THE THIRD, added 2026-08-29. `agent_turn_state` meets the same criterion as the two above -- one
#: row per agent, keyed on agent_id, no filtering, grouping or ordering -- and `_status_turn_signals`
#: read it once per agent inside the refresh loop. Measured with the same counter as the rest of this
#: module: 8 per-agent round-trips became 7.
_TURN_STATE_SQL = ("SELECT agent_id, turn_busy, turn_runtime, turn_updated_at, ready, "
                   "turn_started_at, turn_bridge_id "
                   "FROM agent_turn_state WHERE agent_id IN ({})")


async def _load_by_agent(db, sql_template: str, agent_ids: list[str]) -> dict[str, Any]:
    """One row per agent, keyed by agent_id, in as few round-trips as the parameter limit allows."""
    found: dict[str, Any] = {}
    for start in range(0, len(agent_ids), _MAX_PARAMS_PER_QUERY):
        chunk = agent_ids[start:start + _MAX_PARAMS_PER_QUERY]
        if not chunk:
            continue
        placeholders = ",".join("?" for _ in chunk)
        rows = await (await db.execute(sql_template.format(placeholders), tuple(chunk))).fetchall()
        for row in rows:
            found[str(row["agent_id"])] = row
    return found


class LiveStatusSignals:
    """Reads each signal when asked -- exactly what the inline queries did, and the default."""

    prefetched = False

    async def status_state(self, db, agent_id: str):
        return await (await db.execute(
            "SELECT in_turn, awaiting_input, last_event_at, turn_started_at "
            "FROM agent_status_state WHERE agent_id=?",
            (agent_id,),
        )).fetchone()

    async def console_signal(self, db, agent_id: str):
        return await (await db.execute(
            "SELECT working_at, subagents_at FROM agent_console_signal WHERE agent_id = ?",
            (agent_id,),
        )).fetchone()

    async def turn_state(self, db, agent_id: str):
        return await (await db.execute(
            "SELECT turn_busy, turn_runtime, turn_updated_at, ready, turn_started_at, "
            "turn_bridge_id FROM agent_turn_state "
            "WHERE agent_id = ?",
            (agent_id,),
        )).fetchone()


class PrefetchedStatusSignals:
    """Both signals for a whole batch, read up front. Answers from memory, never touching `db`.

    A MISSING AGENT ANSWERS None, which is what the single-row query returns for an agent with no row
    -- the common case, since these tables are written only once an agent has actually reported. A
    prefetch that fell back to a query for a missing agent would quietly restore the N+1 for exactly
    the agents that are cheapest to answer.
    """

    prefetched = True

    def __init__(self, status_state_rows: dict[str, Any], console_signal_rows: dict[str, Any],
                 turn_state_rows: dict[str, Any] | None = None):
        self._status_state = status_state_rows
        self._console_signal = console_signal_rows
        # Defaulted so a caller constructing this directly with two arguments -- the shape before
        # 2026-08-29 -- still gets an object that answers every question, rather than one that
        # raises on the third the moment a status refresh reaches it.
        self._turn_state = turn_state_rows or {}

    @classmethod
    async def load(cls, db, agent_ids: Iterable[str]) -> "PrefetchedStatusSignals":
        ids = sorted({str(a or "").strip() for a in agent_ids if str(a or "").strip()})
        if not ids:
            return cls({}, {})
        return cls(
            await _load_by_agent(db, _STATUS_STATE_SQL, ids),
            await _load_by_agent(db, _CONSOLE_SIGNAL_SQL, ids),
            await _load_by_agent(db, _TURN_STATE_SQL, ids),
        )

    async def status_state(self, db, agent_id: str):
        return self._status_state.get(str(agent_id))

    async def console_signal(self, db, agent_id: str):
        return self._console_signal.get(str(agent_id))

    async def turn_state(self, db, agent_id: str):
        return self._turn_state.get(str(agent_id))


#: The default every existing caller gets. Stateless, so one instance is correct and shared.
LIVE_STATUS_SIGNALS = LiveStatusSignals()


def status_signals_or_live(status_signals: Optional[Any]):
    """Guards fail closed: an absent prefetch means READ, never means skip."""
    return status_signals if status_signals is not None else LIVE_STATUS_SIGNALS
