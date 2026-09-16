"""Telling the dashboard WHICH data changed, so it refetches that and nothing else.

WHY THIS EXISTS. Measured 2026-09-16 on an idle host: the dashboard refetched ten endpoints every 15
seconds whether or not anything had changed, and almost every pushed event triggered the same full
refetch. The pushes were already on change and nearly free (2 events, 208 bytes in an idle minute);
the cost was the timer and the bundle. A dashboard that knows which TABLES a commit touched can
refetch the parts that read them and drop the timer while its socket is connected.

FED FROM ONE PLACE. Every connection `get_db()` hands out passes its statements through the pool's
checkout (service/db_pool.py). The checkout notes the table each INSERT, UPDATE, DELETE or REPLACE
names, and hands them here only when the transaction COMMITS; a rollback, or a connection returned
without a commit, publishes nothing. No write site has to remember to announce anything, which is
the failure the hand-placed broadcasts have: 46 event names, sent from wherever someone remembered.

IN MEMORY, AND BOUNDED BY CONSTRUCTION. Nothing is written to the database -- a change log there was
rejected (docs/superpowers/plans/2026-09-16-idle-cost-and-change-driven-updates.md): it would add a
write to the hottest paths and a table that needs pruning. There is no ring or replay either: an
event carries a monotonic `seq` and this process's `instance`, and a client that sees a gap or a new
instance refetches everything once. So the only state is the set of tables waiting for the next flush.

LIVENESS IS SAID, NOT SUPPRESSED. Heartbeats, turn markers and streamed terminal output commit many
times a minute and change nothing a list shows except an age. They are declared below, beside the
columns that make them liveness, and reported in a separate field with a longer coalescing window,
so a client can throttle them without losing the fact that something moved. A write whose columns
cannot be read (a SET built at runtime that this parser does not understand) is NOT liveness: an
unrecognised write costs one extra refetch, while a real change mistaken for liveness would sit
unseen.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from functools import lru_cache
from typing import Mapping, Optional

logger = logging.getLogger(__name__)

#: Every column of these tables is liveness. `None` means the whole table.
#: Anything else is judged per UPDATE by the columns it sets, against the sets below.
LIVENESS_WRITES: Mapping[str, Optional[frozenset]] = {
    # Heartbeats and the in-flight turn marker. A derived status that MOVES because of them is
    # published separately, as a change to `agents` (service/reconcilers/status_cache.py).
    "bridge_instances": None,
    "agent_turn_state": None,
    "agent_status_state": None,
    "agent_console_signal": None,
    "claimer_leases": None,
    # One row per flushed output chunk, read only by a console that already streams the output.
    "terminal_events": None,
    # The heartbeat: `last_seen`, and `status` through a CASE that keeps it unless it was stopped.
    "agents": frozenset({"last_seen", "status"}),
    "agent_sessions": frozenset({"last_seen", "telemetry"}),
    "environments": frozenset({"last_seen", "metadata", "cwd_roots"}),
    # The streamed tail and its activity reading. A status change on a terminal is NOT here.
    "terminal_sessions": frozenset({
        "output", "output_at", "output_seq", "updated_at",
        "activity_state", "activity_rule", "activity_observed_at",
    }),
}

#: How long changes gather before one event is sent. A burst of writes from one request, or from a
#: sweep, becomes one event.
COALESCE_S = 0.25
#: The window when ONLY liveness is waiting. A heartbeat lands every few seconds per agent, and the
#: dashboard throttles these far longer than this anyway.
LIVENESS_COALESCE_S = 10.0

_WRITE = re.compile(
    r"""^\s*(?:
        (?P<insert>INSERT(?:\s+OR\s+\w+)?\s+INTO|REPLACE\s+INTO)
      | (?P<update>UPDATE(?:\s+OR\s+\w+)?)
      | (?P<delete>DELETE\s+FROM)
    )\s+["`\[]?(?P<table>[A-Za-z_]\w*)["`\]]?(?P<rest>.*)\Z""",
    re.IGNORECASE | re.DOTALL | re.VERBOSE,
)
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING = re.compile(r"'(?:[^']|'')*'")
_PARENS = re.compile(r"\([^()]*\)")
_CTE = re.compile(r"^\s*WITH\b", re.IGNORECASE)
_WRITE_KEYWORD = re.compile(r"\b(?:INSERT|REPLACE|UPDATE|DELETE)\b", re.IGNORECASE)
_SET_END = re.compile(r"\b(?:WHERE|RETURNING|FROM)\b", re.IGNORECASE)
_ASSIGNED = re.compile(r"(?:^|,)\s*[\"`\[]?([A-Za-z_]\w*)[\"`\]]?\s*=", re.DOTALL)


class Write:
    """One statement's effect: the table, and for an UPDATE the columns it sets (None if unknown)."""

    __slots__ = ("table", "columns")

    def __init__(self, table: str, columns: Optional[frozenset]) -> None:
        self.table = table
        self.columns = columns

    @property
    def liveness(self) -> bool:
        if self.table not in LIVENESS_WRITES:
            return False
        declared = LIVENESS_WRITES[self.table]
        if declared is None:
            return True
        # Only an UPDATE whose every column is declared. An INSERT or DELETE adds or removes a row.
        return self.columns is not None and bool(self.columns) and self.columns <= declared

    def __eq__(self, other) -> bool:
        return isinstance(other, Write) and (self.table, self.columns) == (other.table, other.columns)

    def __repr__(self) -> str:
        return f"Write({self.table!r}, {sorted(self.columns) if self.columns is not None else None})"


def _peel(text: str) -> str:
    previous = None
    while previous != text:
        previous, text = text, _PARENS.sub("", text)
    return text


def _update_columns(rest: str) -> Optional[frozenset]:
    # An alias may sit between the table and SET: `UPDATE terminal_controls AS stale SET ...`.
    body = re.match(r"\s+(?:(?:AS\s+)?(?!SET\b)\w+\s+)?SET\s+(?P<set>.*)\Z", rest, re.IGNORECASE | re.DOTALL)
    if not body:
        return None
    # Nested calls hold commas that are not separators, `COALESCE(a, ?)`, and a subquery holds a
    # WHERE that is not this statement's. Peel them from the inside before looking for the end.
    assignments = _peel(body.group("set"))
    end = _SET_END.search(assignments)
    if end:
        assignments = assignments[: end.start()]
    columns = frozenset(match.group(1).lower() for match in _ASSIGNED.finditer(assignments))
    return columns or None


@lru_cache(maxsize=1024)
def written_table(sql: str) -> Optional[Write]:
    """The write one statement makes, or None for a read, a PRAGMA, or a transaction statement.

    Cached by the SQL text: the service issues a few hundred distinct statements, most of them
    thousands of times, and a read must cost one dictionary lookup here.
    """
    text = _BLOCK_COMMENT.sub(" ", _LINE_COMMENT.sub(" ", str(sql or "")))
    text = _STRING.sub("''", text)
    if _CTE.match(text):
        # `WITH x AS (...) UPDATE ...`: the write follows the peeled common table expressions.
        text = _peel(text)
        keyword = _WRITE_KEYWORD.search(text)
        if not keyword:
            return None
        text = text[keyword.start():]
    match = _WRITE.match(text)
    if not match:
        return None
    table = match.group("table").lower()
    columns = _update_columns(match.group("rest")) if match.group("update") else frozenset()
    return Write(table, columns)


def written_tables_of_script(script: str) -> list[Write]:
    """Every write in a script, statement by statement. Only used by `executescript`, which is rare."""
    return [write for write in (written_table(part) for part in str(script or "").split(";")) if write]


class ChangeFeed:
    """Committed table changes, coalesced into `data_changed` events for every dashboard socket."""

    def __init__(self) -> None:
        self.instance = uuid.uuid4().hex
        self.seq = 0
        self._manager = None
        self._changed: set[str] = set()
        self._liveness: set[str] = set()
        self._flush_handle: Optional[asyncio.TimerHandle] = None
        self._flush_at: float = 0.0

    # ── lifecycle ────────────────────────────────────────────────────────────────────────────────
    def attach(self, manager) -> None:
        """Start publishing to `manager` (service/ws.py). Until then every change is dropped."""
        self._manager = manager

    def detach(self) -> None:
        self._manager = None
        if self._flush_handle is not None:
            self._flush_handle.cancel()
            self._flush_handle = None
        self._changed.clear()
        self._liveness.clear()

    # ── input ────────────────────────────────────────────────────────────────────────────────────
    def committed(self, writes) -> None:
        """A transaction committed these writes."""
        if self._manager is None:
            return
        for write in writes:
            if write.liveness:
                self._liveness.add(write.table)
            else:
                self._changed.add(write.table)
        self._schedule()

    def agent_status_moved(self, agent_id: str) -> None:
        """An agent's DERIVED status changed without a write that says so -- a lease that ran out."""
        if self._manager is None:
            return
        self._changed.add("agents")
        self._schedule()

    # ── output ───────────────────────────────────────────────────────────────────────────────────
    def pending(self) -> tuple[frozenset, frozenset]:
        return frozenset(self._changed), frozenset(self._liveness - self._changed)

    def _schedule(self) -> None:
        if not self._changed and not self._liveness:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        delay = COALESCE_S if self._changed else LIVENESS_COALESCE_S
        due = loop.time() + delay
        if self._flush_handle is not None:
            if self._flush_at <= due:
                return
            # Liveness was waiting its long window and a real change arrived: send sooner.
            self._flush_handle.cancel()
        self._flush_at = due
        self._flush_handle = loop.call_at(due, lambda: loop.create_task(self.flush()))

    async def flush(self) -> Optional[dict]:
        """Send what is waiting as one event. Returns the payload sent, or None."""
        self._flush_handle = None
        changed, liveness = self.pending()
        self._changed.clear()
        self._liveness.clear()
        manager = self._manager
        if manager is None or (not changed and not liveness):
            return None
        self.seq += 1
        payload = {
            "seq": self.seq,
            "instance": self.instance,
            "tables": sorted(changed),
            "liveness": sorted(liveness),
        }
        try:
            await manager.broadcast("data_changed", payload)
        except Exception:  # noqa: BLE001 -- a failed push must never fail anything else
            logger.exception("data_changed push failed")
        return payload


#: The one feed. Single-process by the same rule as the live-status cache (CLAUDE.md).
CHANGE_FEED = ChangeFeed()
