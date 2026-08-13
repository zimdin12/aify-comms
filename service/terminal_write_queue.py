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

    async def _write_terminal_output(self, terminal_id: str, output: str, *, status: str = "", seq: int = 0) -> None:
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
            await _append_terminal_output(db, terminal, output, status=status, seq=seq or int(terminal["output_seq"] or 0))
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
            elif norm_status in {"attached", "running", "live", "idle", "starting", "stopping"}:
                # Mirror the live terminal status onto the session so the
                # status engine sees the console advance past "starting".
                # Without this agent_sessions.terminal_status stays "starting"
                # forever and the engine reports a permanent transitioning
                # "working" even for an idle console.
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
