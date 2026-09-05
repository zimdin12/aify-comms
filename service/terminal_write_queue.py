"""The terminal-output write queue: one process-global instance, batching row writes.

PLACEMENT — service level, not `api_core/`, and the reason is a rule this module would otherwise
break. Every `api_core` leaf is documented as taking `db` explicitly and owning no transaction: no
`get_db(`, no `.commit(`, no `.rollback(`. This class opens its own connection and commits, because
BATCHING THE WRITE IS ITS ENTIRE JOB — the transaction is the responsibility, not a leak of one. So it
sits beside `terminal_snapshot.py` and `terminal_diagnostics.py`, which is where behaviour extracted
out of the control plane goes when it is not a pure leaf. Keeping the api_core rule absolute is worth
more than filing this in the same folder as the pure helpers.

WHY IT COULD NOT MOVE UNTIL NOW: it calls `_append_terminal_output`, which lived in
`routers/terminals.py`, so this class depended UPWARD on a router. That call, not this file's size,
is what pinned 233 lines into the carrier. The dependency was reversed one slice earlier.

SINGLE INSTANCE, AND THAT IS LOAD-BEARING. `TERMINAL_OUTPUT_WRITES` is mutable process-global state:
pending deques keyed by terminal, asyncio locks, and scheduled flush tasks. It is only correct with
ONE uvicorn process and one event loop — the same constraint that governs `_LIVE_STATE_CACHE` — and
it is only correct with ONE INSTANCE inside that process. A second `TerminalOutputWriteQueue()`
anywhere would silently split the pending writes across two queues with two independent flush
timers, so every consumer must import THIS binding rather than construct its own. The declaration
lives here, next to the class, for that reason.
"""

from __future__ import annotations

import asyncio
from collections import deque
from typing import Any

from service.api_core.terminal_output import _append_terminal_output
from service.api_core.terminal_tail_buffer import TAIL_FLUSH_INTERVAL_SECONDS, pending
from service.api_core.terminal_status import TERMINAL_STOPPABLE_STATUSES
from service.clock import now as _now
from service.db import get_db
from service.reconcilers.status_cache import invalidate_agent_live_state as _invalidate_agent_live_state


class TerminalOutputWriteQueue:
    def __init__(
        self,
        *,
        idle_flush_ms: int = 4,
        max_latency_ms: int = 24,
        max_batch_chars: int = 16 * 1024,
        max_pending_chars: int = 256 * 1024,
    ):
        self.idle_flush_seconds = max(0.001, idle_flush_ms / 1000)
        self.max_latency_seconds = max(self.idle_flush_seconds, max_latency_ms / 1000)
        self.max_batch_chars = max(1024, int(max_batch_chars))
        self.max_pending_chars = max(self.max_batch_chars, int(max_pending_chars))
        self._pending: dict[str, dict[str, Any]] = {}
        self._idle_handles: dict[str, asyncio.Handle] = {}
        self._max_handles: dict[str, asyncio.Handle] = {}
        self._settle_handles: dict[str, asyncio.Handle] = {}
        self._flush_tasks: dict[str, asyncio.Task] = {}
        # Highest seq ever issued per terminal. Guarantees strict monotonicity
        # across pending-state recreation even if a concurrent request reads a
        # stale output_seq from the DB while a prior flush hasn't committed yet
        # (otherwise seq could regress and the dashboard's seq-dedupe would
        # silently drop fresh output).
        self._seq_floor: dict[str, int] = {}
        # Set by the output endpoint so the queue can emit ONE ordered,
        # gap-free terminal_output broadcast per flush. Per-POST broadcast
        # reordered vs seq under concurrency, causing the dashboard's
        # seq-dedupe to drop frames -> ANSI desync -> scrambled console.
        self.ws_manager = None
        self._lock = asyncio.Lock()
        # ponytail: SQLite has one writer; queue high-rate terminal flushes here instead of
        # letting per-terminal tasks create a retry storm against the database lock.
        self._write_lock = asyncio.Lock()

    async def enqueue(self, terminal_id: str, output: str = "", *, status: str = "", base_seq: int = 0, autoschedule: bool = True) -> int:
        chunk = str(output or "")
        terminal_status = str(status or "").strip()
        if not terminal_id or (not chunk and not terminal_status):
            return 0
        flush_now = False
        async with self._lock:
            state = self._pending.get(terminal_id)
            if not state:
                seq_start = max(int(base_seq or 0), int(self._seq_floor.get(terminal_id, 0)))
                state = {"chunks": deque(), "chars": 0, "status": "", "dropped": 0, "last_seq": seq_start}
                self._pending[terminal_id] = state
                if autoschedule:
                    self._schedule_max_flush_locked(terminal_id)
            state["last_seq"] = int(state.get("last_seq") or 0) + 1
            self._seq_floor[terminal_id] = state["last_seq"]
            if chunk:
                state["chunks"].append(chunk)
                state["chars"] += len(chunk)
                self._bound_pending_locked(state)
            if terminal_status:
                state["status"] = terminal_status
            if not autoschedule:
                return int(state["last_seq"])
            flush_now = state["chars"] >= self.max_batch_chars or terminal_status in {"stopped", "failed"}
            if flush_now:
                self._schedule_flush_locked(terminal_id, delay=0)
            else:
                self._schedule_idle_flush_locked(terminal_id)
            return int(state["last_seq"])

    def _bound_pending_locked(self, state: dict[str, Any]) -> None:
        chunks = state["chunks"]
        while state["chars"] > self.max_pending_chars and chunks:
            removed = chunks.popleft()
            removed_len = len(removed)
            state["chars"] -= removed_len
            state["dropped"] += removed_len

    def _schedule_idle_flush_locked(self, terminal_id: str) -> None:
        handle = self._idle_handles.pop(terminal_id, None)
        if handle:
            handle.cancel()
        self._idle_handles[terminal_id] = asyncio.get_running_loop().call_later(
            self.idle_flush_seconds,
            self._schedule_flush_from_timer,
            terminal_id,
        )

    def _schedule_max_flush_locked(self, terminal_id: str) -> None:
        handle = self._max_handles.pop(terminal_id, None)
        if handle:
            handle.cancel()
        self._max_handles[terminal_id] = asyncio.get_running_loop().call_later(
            self.max_latency_seconds,
            self._schedule_flush_from_timer,
            terminal_id,
        )

    def _track_flush_task(self, terminal_id: str, task: asyncio.Task) -> None:
        self._flush_tasks[terminal_id] = task
        task.add_done_callback(lambda done, key=terminal_id: self._on_flush_done(key, done))

    def _on_flush_done(self, terminal_id: str, task: asyncio.Task) -> None:
        self._flush_tasks.pop(terminal_id, None)
        try:
            task.result()
        except BaseException:
            if terminal_id in self._pending:
                try:
                    asyncio.get_running_loop().call_later(0.1, self._schedule_flush_from_timer, terminal_id)
                except RuntimeError:
                    pass

    def _schedule_flush_from_timer(self, terminal_id: str) -> None:
        try:
            self._track_flush_task(terminal_id, asyncio.create_task(self.flush_terminal(terminal_id)))
        except RuntimeError:
            # No active loop; the next explicit flush will persist the backlog.
            return

    def _schedule_flush_locked(self, terminal_id: str, *, delay: float) -> None:
        next_delay = delay if delay > 0 else 0.001
        asyncio.get_running_loop().call_later(next_delay, self._schedule_flush_from_timer, terminal_id)

    async def flush_terminal(self, terminal_id: str) -> None:
        existing = self._flush_tasks.get(terminal_id)
        if existing and existing is not asyncio.current_task():
            await asyncio.shield(existing)
            return
        async with self._lock:
            state = self._pending.pop(terminal_id, None)
            idle_handle = self._idle_handles.pop(terminal_id, None)
            max_handle = self._max_handles.pop(terminal_id, None)
            if idle_handle:
                idle_handle.cancel()
            if max_handle:
                max_handle.cancel()
        if not state:
            return
        prefix = ""
        if state["dropped"]:
            prefix = f"[aify-comms dropped {state['dropped']} chars from terminal output backlog]\n"
        output = prefix + "".join(state["chunks"])
        status = state["status"]
        seq = int(state.get("last_seq") or 0)
        try:
            async with self._write_lock:
                await self._write_terminal_output(terminal_id, output, status=status, seq=seq)
        except BaseException:
            await self._requeue_front(terminal_id, output, status=status, seq=seq)
            raise
        # THE STREAM MAY HAVE JUST STOPPED. The tail is written on the NEXT chunk once the interval
        # has passed -- and when output stops there is no next chunk, so the last frame was held for
        # ever. Two readers ask exactly then: the idle-prompt hint that closes a finished run, and
        # the hermes resume check that gates claiming channel work. Both read the stored column with
        # no live-screen path, so both saw a tail ending before the frame they needed.
        self._schedule_settle(terminal_id)

    def _schedule_settle(self, terminal_id: str) -> None:
        """Write the held tail shortly, unless more output writes it first.

        SCHEDULED FROM THE FLUSH, which is already coalesced, so a terminal streaming at ten
        chunks a second schedules one settle per flush rather than per chunk. Each supersedes the
        last, and one that finds nothing held returns without touching the database.

        A LITTLE OVER THE INTERVAL, so an ordinary write that is about to happen anyway wins the
        race and the settle becomes the no-op it should be.
        """
        if pending(terminal_id) is None:
            return
        handle = self._settle_handles.pop(terminal_id, None)
        if handle:
            handle.cancel()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return          # no loop (a synchronous test); the next chunk still writes it
        self._settle_handles[terminal_id] = loop.call_later(
            TAIL_FLUSH_INTERVAL_SECONDS + 0.25, self._settle_from_timer, terminal_id,
        )

    def _settle_from_timer(self, terminal_id: str) -> None:
        self._settle_handles.pop(terminal_id, None)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        asyncio.create_task(self.settle_terminal_tail(terminal_id))

    async def settle_terminal_tail(self, terminal_id: str) -> None:
        """Write a held tail for a terminal whose output has stopped.

        UNDER THE SAME WRITE LOCK as every other write to this column: the read-modify-write in
        `_append_terminal_output` loses one of two concurrent writers outright, which is what that
        lock exists for.
        """
        held = pending(terminal_id)
        if held is None:
            return
        try:
            async with self._write_lock:
                await self._write_terminal_output(
                    terminal_id, "", seq=int(held.get("seq") or 0), settle=True,
                )
        except Exception:
            # BEST EFFORT. A settle that fails leaves the tail held, and the next chunk or the next
            # settle writes it -- the state before this existed. Raising here would surface inside a
            # bare timer task with nobody to catch it.
            pass

    async def _requeue_front(self, terminal_id: str, output: str, *, status: str = "", seq: int = 0) -> None:
        if not output and not status:
            return
        async with self._lock:
            state = self._pending.get(terminal_id)
            if not state:
                state = {"chunks": deque(), "chars": 0, "status": "", "dropped": 0, "last_seq": int(seq or 0)}
                self._pending[terminal_id] = state
            if output:
                state["chunks"].appendleft(output)
                state["chars"] += len(output)
                self._bound_pending_locked(state)
            if status:
                state["status"] = status
            if seq:
                state["last_seq"] = max(int(state.get("last_seq") or 0), int(seq))

    async def append_outside_the_queue(self, db, terminal_id: str, output: str, *, status: str = "",
                                       fallback=None) -> None:
        """Append output for a caller that must write IMMEDIATELY, under this queue's write lock.

        IT RE-READS THE ROW INSIDE THE LOCK, and that is the whole fix rather than a detail. Taking
        the lock around the append ALONE does not work: `_append_terminal_output` derives `current`
        from the row it is handed, so two callers that each read the row and then queue up on the
        lock still overwrite each other -- the second one appends to a value that was already stale
        when it arrived. The first version of this method did exactly that and the test caught it,
        keeping only "BBBB" of "AAAA"+"BBBB". `_write_terminal_output` was right all along for the
        same reason: its SELECT is inside the locked region.

        WHY IT EXISTS, proven 2026-09-03. `_append_terminal_output` is a read-modify-write: it takes
        `current` from the row it is handed, concatenates, trims and UPDATEs. Two callers doing that
        at once lose one of the two writes outright -- measured against the real function, two
        interleaved appends of "AAAA" and "BBBB" stored "BBBB" alone, while the same two serialised
        stored both. Every streamed frame already goes through this queue and is serialised by
        `_write_lock`; the control-completion path called the helper DIRECTLY and held no lock, so a
        control reporting output while a flush was in flight silently discarded one side's bytes.

        THE LOCK RATHER THAN THE QUEUE, deliberately. Routing that caller through `enqueue` would fix
        the race too and is the tidier shape, but it changes control output from immediate to
        batched on the hottest write path in the service. This takes the same lock and changes
        nothing else, which is the smallest change that closes it.

        NOT ATOMIC SQL, which is the usual answer and is wrong here: `substr(output || ?, -65536)`
        would drop `_trim_terminal_output`'s line-boundary trim, and a tail cut mid-ANSI-escape is
        what made the dashboard seed a fresh xterm with garbage (fixed 2026-06-07).
        """
        async with self._write_lock:
            terminal = await (await db.execute(
                """
                SELECT id, session_id, agent_id, environment_id, bridge_id, runtime,
                       output, status, output_seq, created_at, cols, rows
                FROM terminal_sessions WHERE id = ?
                """,
                (terminal_id,),
            )).fetchone()
            # A row that vanished between the caller's read and this one: fall back to what the
            # caller already had rather than silently writing nothing, which is what it did before.
            await _append_terminal_output(db, terminal if terminal else fallback, output, status=status)

    async def _write_terminal_output(
        self, terminal_id: str, output: str, *, status: str = "", seq: int = 0, settle: bool = False,
    ) -> None:
        db = await get_db()
        try:
            terminal = await (await db.execute(
                """
                SELECT id, session_id, agent_id, environment_id, bridge_id, runtime,
                       output, status, output_seq, created_at, cols, rows
                FROM terminal_sessions WHERE id = ?
                """,
                (terminal_id,),
            )).fetchone()
            if not terminal:
                return
            await _append_terminal_output(
                db, terminal, output, status=status,
                seq=seq or int(terminal["output_seq"] or 0), settle=settle,
            )
            norm_status = str(status or "").strip().lower()
            if norm_status in {"stopped", "failed"}:
                await db.execute(
                    """
                    UPDATE agent_sessions
                    SET terminal_status = ?,
                        owner_mode = 'managed',
                        last_seen = ?
                    WHERE id = ?
                    """,
                    (norm_status, _now(), terminal["session_id"]),
                )
            elif norm_status in TERMINAL_STOPPABLE_STATUSES:
                # Mirror the live terminal status onto the session so the
                # status engine sees the console advance past "starting".
                # Without this agent_sessions.terminal_status stays "starting"
                # forever and the engine reports a permanent transitioning
                # "working" even for an idle console.
                #
                # THE SET IS THE VOCABULARY'S, NOT THIS FILE'S. It was hand-typed as
                # {attached, running, live, idle, starting, stopping}: missing `active` and
                # `recovering`, and carrying `live`, which is not a terminal status -- the
                # transition rule refuses anything outside `TERMINAL_SESSION_STATUSES`, and `live`
                # is not in it. So a terminal reporting `active` or `recovering` left the session's
                # mirrored status stale, which is exactly the harm the paragraph above names.
                # Latent rather than live: the bridge sends `attached` and `failed` with output.
                #
                # The `stopped`/`failed` branch above is deliberately NOT widened to the full end
                # vocabulary here. It also writes `owner_mode`, and the ROUTE already closes out
                # every end status through `_close_out_terminal_on_end_status` with
                # `_TERMINAL_END_STATUSES` -- so widening this one would duplicate that with
                # different side effects rather than complete it.
                await db.execute(
                    """
                    UPDATE agent_sessions
                    SET terminal_status = ?,
                        last_seen = ?
                    WHERE id = ?
                    """,
                    (norm_status, _now(), terminal["session_id"]),
                )
            await _invalidate_agent_live_state(db, terminal["agent_id"])
            await db.commit()
        finally:
            await db.close()
        # Ordered, post-commit, coalesced broadcast — the single source of
        # live terminal output for the dashboard. Best-effort.
        if self.ws_manager is not None and (output or norm_status):
            try:
                await self.ws_manager.broadcast(
                    "terminal_output",
                    {
                        "terminalId": terminal_id,
                        "agentId": str(terminal["agent_id"] or ""),
                        "status": norm_status,
                        "output": output or "",
                        "seq": seq or int(terminal["output_seq"] or 0),
                    },
                )
            except BaseException:
                pass

    async def flush_all(self) -> None:
        while True:
            async with self._lock:
                ids = list(self._pending.keys())
            if not ids:
                return
            for terminal_id in ids:
                await self.flush_terminal(terminal_id)


TERMINAL_OUTPUT_WRITES = TerminalOutputWriteQueue()

async def flush_terminal_output_writes_for_tests() -> None:
    await TERMINAL_OUTPUT_WRITES.flush_all()


def discard_terminal_output_writes_for_tests() -> None:
    """Drop everything the queue is still holding, as a process restart would.

    THE HAZARD THIS CLOSES, measured 2026-08-26. The queue is a process-global and the suite is one
    process, but each test gets a FRESH DATABASE -- so the two disagree about what a process is. A
    terminal's exit POST schedules its flush through `call_later`, which does not run before the
    request returns; the pending chunks and the `_seq_floor` then survive into the NEXT test and
    flush into ITS database. Observed directly: a terminal seeded with `output=''` in setUp read
    back nine `[terminal exited]` markers and `outputSeq: 11`, none of which that test wrote.

    Nothing was wrong when this was found -- the suite was green, and the sibling flush hook above
    exists for tests that DO want their own bytes drained. The danger is the other direction: a test
    can be handed output it never produced and conclude a write path worked. That is the shape this
    project keeps paying for, so the reset is routine rather than triggered by a surprise.

    NOT A PRODUCT PATH. One service process, one database, one loop: there the pending writes always
    reach the row they were queued for, which is the whole design of the batching. This exists
    because a test suite simulates a process boundary the class was never told about.
    """
    queue = TERMINAL_OUTPUT_WRITES
    for handles in (queue._idle_handles, queue._max_handles):
        for handle in handles.values():
            handle.cancel()
        handles.clear()
    for task in queue._flush_tasks.values():
        task.cancel()
    queue._flush_tasks.clear()
    queue._pending.clear()
    # The seq floor goes too. It is monotonic ACROSS a terminal's lifetime, and carrying a previous
    # database's high-water mark into a fresh one makes the first frame of a new terminal claim a
    # sequence eleven ahead of anything written -- which is exactly what the measurement showed.
    queue._seq_floor.clear()
