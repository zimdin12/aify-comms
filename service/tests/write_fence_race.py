"""A rival write that races a handler's guard read, and the database dump that shows a refusal wrote nothing (B2).

THE RACE IS ORDERED BY THE DATABASE, NOT BY THE CLOCK. The rival tries its write the moment the handler's read
returns, from inside that read, with a busy timeout of zero. If the handler holds its write fence, SQLite refuses the
rival at once and the rival waits for the lock. If it does not, the rival commits on the spot, before the handler's
next statement. No sleep orders anything. The long deadline below only bounds a broken run.

The rival runs on its own thread and event loop with its own connection, so its change can be the product's own
async write (a rename is `_rewrite_agent_references_for_rename`) rather than a copy of it.

WHICH ORDER HAPPENED IS READ FROM THE DATABASE: inside its own write transaction, which no other writer can
interleave, the rival reads whether the handler's write is already committed. True means the handler committed
first. False means the rival did, and then the handler must have refused.
"""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading

import aiosqlite

#: Bounds a broken run, never orders a working one.
DEADLINE_SECONDS = 30


def dump(db_path) -> str:
    """Every row of every table, as SQL: two equal dumps are two logically equal databases."""
    conn = sqlite3.connect(str(db_path))
    try:
        return "\n".join(conn.iterdump())
    finally:
        conn.close()


async def define(db, agent_id: str, machine_id: str) -> None:
    """The row every definition guard reads, written the way a push commits it: under the write lock."""
    body = {"id": agent_id, "name": agent_id, "role": "coder", "harness": "claude", "mode": "managed",
            "workspace": "/work", "model": "", "effort": "", "instructions": "", "env": {}, "herdrSpace": True}
    await db.execute(
        "INSERT INTO agent_definitions (agent_id, machine_id, store_id, incarnation, revision, definition_digest, body,"
        " available, updated_at) VALUES (?, ?, 's-rival', 1, 1, 'digest-rival', ?, 1, 'now')",
        (agent_id, machine_id, json.dumps(body)))


class Rival:
    """Runs `change(db)` in its own write transaction right after the handler's read. `committed_before(db)` reads,
    inside that transaction, whether the handler's write is already there. Both are async, given the rival's
    connection."""

    def __init__(self, db_path, change, committed_before):
        self.db_path, self.change, self.committed_before = str(db_path), change, committed_before
        self.blocked = False
        self.handler_committed_first = None
        self.committed = False
        self.error = None
        self._settled = threading.Event()
        self._thread = None

    def attempt(self) -> None:
        """Called from inside the wrapped read. Returns once the rival has either committed or been refused the lock,
        so in both cases that outcome precedes the handler's next statement."""
        self._thread = threading.Thread(target=lambda: asyncio.run(self._run()))
        self._thread.start()
        if not self._settled.wait(DEADLINE_SECONDS):
            raise AssertionError("the rival neither committed nor was refused the lock")

    async def _run(self) -> None:
        try:
            db = await aiosqlite.connect(self.db_path, timeout=0)
            try:
                await db.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as error:
                await db.close()
                if "locked" not in str(error):
                    raise
                self.blocked = True
                self._settled.set()
                db = await aiosqlite.connect(self.db_path, timeout=DEADLINE_SECONDS)
                await db.execute("BEGIN IMMEDIATE")
            try:
                db.row_factory = aiosqlite.Row
                self.handler_committed_first = bool(await self.committed_before(db))
                await self.change(db)
                await db.commit()
                self.committed = True
            finally:
                await db.close()
        except Exception as error:  # surfaced by finish(); a thread's exception is otherwise lost
            self.error = error
        finally:
            self._settled.set()

    def finish(self) -> None:
        if self._thread is not None:
            self._thread.join(DEADLINE_SECONDS)
            if self._thread.is_alive():
                raise AssertionError("the rival never got the write lock: the handler held it past the deadline")
        if self.error is not None:
            raise self.error


def racing(module, name: str, rival: Rival):
    """Wrap `module.<name>`, the handler's read, so the rival tries its write as soon as the read returns.
    Returns the restore function."""
    real = getattr(module, name)

    async def guarded(*args, **kwargs):
        answer = await real(*args, **kwargs)
        rival.attempt()
        return answer

    setattr(module, name, guarded)
    return lambda: setattr(module, name, real)
