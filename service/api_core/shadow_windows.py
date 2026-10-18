"""The status shadow comparison, persisted in version-bound windows (status-switch plan S2a).

A WINDOW is one stretch of continuous recording on one version triple (comms, env, wrapper build). It closes, and
the next one opens, when the service restarts, when a publication loss begins or ends, or when a version changes.
A closed window is never reopened or merged, so an interruption stays visible as a gap between windows rather than
disappearing into a running total. Its counts are deltas taken from the in-process recorder.

A restart cannot write its own close, so the next start closes the dangling window AT ITS LAST FLUSH, the last
moment it is known to have been recording. Diagnostic only: nothing reads these tables to decide a status.
"""
import asyncio
import logging
from dataclasses import dataclass

from service import clock
from service.db import get_db
from service.api_core import partial_status_shadow as shadow

logger = logging.getLogger(__name__)

OWNER = 'status word'
FLUSH_SECONDS = 30


@dataclass(frozen=True)
class Versions:
    comms: str
    env: str | None = None  # null until aify-env publishes its build (B6); never guessed
    wrapper: str | None = None


class ShadowWindows:
    """The current window and the rule for closing it. Has identity and state, so an object."""

    def __init__(self, recorder=None):
        self._recorder = recorder
        self._window = None
        self._versions = None
        self._in_loss = False
        self._flush_lost = False

    @property
    def recorder(self):
        return self._recorder or shadow.recorder

    async def start(self, versions, at):
        db = await get_db()
        try:
            await db.execute('BEGIN IMMEDIATE')
            await db.execute("UPDATE status_shadow_windows SET closed_at = flushed_at, close_reason = 'service-restart' "
                             "WHERE closed_at IS NULL")
            self._window = await self._open(db, versions, at)
            await db.commit()
        finally:
            await db.close()
        self._versions = versions
        self.recorder.take()  # anything recorded before this window opened belongs to no window

    async def tick(self, versions, at):
        """Flush what was recorded into the current window, then close it if this flush ends its continuity."""
        pending, loss = self.recorder.take()
        # A failed flush lost the counts it had taken, so the window it was flushing is no longer continuous.
        reason = ('flush-lost' if self._flush_lost else
                  'version-change' if versions != self._versions else
                  'publication-loss' if loss and not self._in_loss else
                  'publication-recovered' if self._in_loss and not loss else None)
        self._flush_lost = True  # until this flush commits
        db = await get_db()
        try:
            await db.execute('BEGIN IMMEDIATE')
            # A version change is noticed after the counts were recorded, so they belong to the window being closed.
            for (source, name), count in pending.items():
                await db.execute(
                    'INSERT INTO status_shadow_window_counts (window_id, owner, source, name, count) VALUES (?, ?, ?, ?, ?) '
                    'ON CONFLICT (window_id, owner, source, name) DO UPDATE SET count = count + excluded.count',
                    (self._window, OWNER, source, name, count))
            await db.execute('UPDATE status_shadow_windows SET flushed_at = ? WHERE id = ?', (at, self._window))
            if reason:
                await db.execute('UPDATE status_shadow_windows SET closed_at = ?, close_reason = ? WHERE id = ?',
                                 (at, reason, self._window))
                self._window = await self._open(db, versions, at)
            await db.commit()
        finally:
            await db.close()
        self._versions, self._in_loss, self._flush_lost = versions, loss, False

    @staticmethod
    async def _open(db, versions, at):
        cursor = await db.execute(
            'INSERT INTO status_shadow_windows (comms_build, env_build, wrapper_build, opened_at, flushed_at) '
            'VALUES (?, ?, ?, ?, ?)', (versions.comms, versions.env, versions.wrapper, at, at))
        return cursor.lastrowid


async def recent_windows(limit=20):
    """The newest windows first, each with its counts as {owner: {source: {name: count}}}."""
    db = await get_db()
    try:
        cursor = await db.execute(
            'SELECT id, comms_build, env_build, wrapper_build, opened_at, flushed_at, closed_at, close_reason '
            'FROM status_shadow_windows ORDER BY id DESC LIMIT ?', (limit,))
        windows = [dict(id=r[0], versions=dict(comms=r[1], env=r[2], wrapper=r[3]), openedAt=r[4], flushedAt=r[5],
                        closedAt=r[6], closeReason=r[7], counts={}) for r in await cursor.fetchall()]
        by_id = {w['id']: w for w in windows}
        if by_id:
            marks = ','.join('?' * len(by_id))
            cursor = await db.execute(
                f'SELECT window_id, owner, source, name, count FROM status_shadow_window_counts WHERE window_id IN ({marks})',
                tuple(by_id))
            for window_id, owner, source, name, count in await cursor.fetchall():
                by_id[window_id]['counts'].setdefault(owner, {}).setdefault(source, {})[name] = count
        return windows
    finally:
        await db.close()


WINDOWS = ShadowWindows()


async def run_periodic(versions):
    """The service's loop: one window per start, flushed every FLUSH_SECONDS. A failed flush is logged, never raised."""
    await WINDOWS.start(versions, clock.now())
    while True:
        await asyncio.sleep(FLUSH_SECONDS)
        try:
            await WINDOWS.tick(versions, clock.now())
        except Exception as error:
            logger.warning('status shadow window flush failed: %s', error)
