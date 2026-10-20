"""A definition that races a handler's guard read, and the database dump that shows a refusal wrote nothing (B2).

THE RACE IS ORDERED BY THE DATABASE, NOT BY THE CLOCK. The rival tries its write the moment the handler's guard
read returns, from inside that read, with a busy timeout of zero. If the handler holds its write fence, SQLite
refuses the rival at once and the rival waits for the lock on a thread. If it does not, the rival commits on the
spot, before the handler writes anything. No sleep orders anything. The long timeout below only bounds a broken run.

WHICH ORDER HAPPENED IS READ FROM THE DATABASE: inside its own write transaction, which no other writer can
interleave, the rival reads whether the handler's write is already committed. True means the handler committed
first. False means the rival did, and then the handler must have refused.
"""
from __future__ import annotations

import json
import sqlite3
import threading

#: Bounds a broken run, never orders a working one.
DEADLINE_SECONDS = 30


def dump(db_path) -> str:
    """Every row of every table, as SQL: two equal dumps are two byte-equal databases."""
    conn = sqlite3.connect(str(db_path))
    try:
        return "\n".join(conn.iterdump())
    finally:
        conn.close()


def define_directly(conn, agent_id: str, machine_id: str) -> None:
    """The row every definition guard reads, written the way a push commits it: under the write lock."""
    body = {"id": agent_id, "name": agent_id, "role": "coder", "harness": "claude", "mode": "managed",
            "workspace": "/work", "model": "", "effort": "", "instructions": "", "env": {}, "herdrSpace": True}
    conn.execute(
        "INSERT INTO agent_definitions (agent_id, machine_id, store_id, incarnation, revision, definition_digest, body,"
        " available, updated_at) VALUES (?, ?, 's-rival', 1, 1, 'digest-rival', ?, 1, 'now')",
        (agent_id, machine_id, json.dumps(body)))


class Rival:
    """Defines `agent_id` right after the handler's guard read. `committed_before(conn)` reads, inside the rival's
    own write transaction, whether the handler's write is already there."""

    def __init__(self, db_path, agent_id: str, machine_id: str, committed_before):
        self.db_path, self.agent_id, self.machine_id = str(db_path), agent_id, machine_id
        self.committed_before = committed_before
        self.blocked = False
        self.handler_committed_first = None
        self.committed = False
        self.error = None
        self._thread = None

    def attempt(self) -> None:
        """Called from inside the wrapped guard read, so it runs before the handler's next statement."""
        conn = sqlite3.connect(self.db_path, timeout=0, isolation_level=None, check_same_thread=False)
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as error:
            conn.close()
            if "locked" not in str(error):
                raise
            self.blocked = True
            self._thread = threading.Thread(target=self._when_free)
            self._thread.start()
            return
        self._commit(conn)

    def _when_free(self) -> None:
        try:
            conn = sqlite3.connect(self.db_path, timeout=DEADLINE_SECONDS, isolation_level=None)
            conn.execute("BEGIN IMMEDIATE")
            self._commit(conn)
        except Exception as error:  # surfaced by finish(); a thread's exception is otherwise lost
            self.error = error

    def _commit(self, conn) -> None:
        try:
            conn.row_factory = sqlite3.Row
            self.handler_committed_first = bool(self.committed_before(conn))
            define_directly(conn, self.agent_id, self.machine_id)
            conn.execute("COMMIT")
            self.committed = True
        finally:
            conn.close()

    def finish(self) -> None:
        if self._thread is not None:
            self._thread.join(DEADLINE_SECONDS)
            if self._thread.is_alive():
                raise AssertionError("the rival never got the write lock: the handler held it past the deadline")
        if self.error is not None:
            raise self.error


def racing(module, name: str, rival: Rival):
    """Wrap `module.<name>`, the handler's guard read, so the rival tries its write as soon as the read returns.
    Returns the restore function."""
    real = getattr(module, name)

    async def guarded(*args, **kwargs):
        answer = await real(*args, **kwargs)
        rival.attempt()
        return answer

    setattr(module, name, guarded)
    return lambda: setattr(module, name, real)
