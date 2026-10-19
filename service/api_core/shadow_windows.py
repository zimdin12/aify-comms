"""The status shadow comparison, persisted in version-bound windows (status-switch plan S2a).

A WINDOW is one stretch of continuous recording on one version triple (comms, env, wrapper build). It closes, and
the next one opens, when the service restarts, when a publication loss begins or ends, when a version changes, or
when its evidence rotates (shadow_evidence_store: a size or age bound, tested after this tick's append).
A closed window is never reopened or merged, so an interruption stays visible as a gap between windows rather than
disappearing into a running total. Its counts are deltas taken from the in-process recorder.

A restart cannot write its own close, so the next start closes the dangling window AT ITS LAST FLUSH, the last
moment it is known to have been recording. Diagnostic only: nothing reads these tables to decide a status.

THE OWNER'S STATE CHANGES ONLY AFTER A COMMIT. A window opened inside a transaction that then fails was never
durable, so adopting its id would point the next flush at a row that does not exist.
"""
import asyncio
import logging
from dataclasses import dataclass

from service import clock
from service.db import get_db
from service.api_core import partial_status_shadow as shadow, shadow_evidence_store as evidence_store
from service.api_core.shadow_evidence import BOOT_ID

logger = logging.getLogger(__name__)

FLUSH_SECONDS = 30


@dataclass(frozen=True)
class Versions:
    comms: str
    env: str | None = None  # null until aify-env publishes its build (B6); never guessed
    wrapper: str | None = None


def next_reason(*, flush_lost, versions_changed, in_loss, lost_seen, healthy_seen):
    """PURE. Why this flush ends the current window (None to keep it), and the scopes still lost after it.

    `in_loss` and `lost_seen` are scopes: publisher keys, ALL_PUBLISHERS, or None when the scope could not be told.
    A scope recovers only on positive evidence FOR IT, with no loss of it observed in the same batch: a completed
    comparison of that publisher, or any completed comparison for ALL_PUBLISHERS (one proves the view was readable).
    A quiet batch, or a comparison of another publisher, proves nothing; an unknown scope never recovers on its own.
    The window closes when the first scope is lost and again when the last one recovers.
    """
    def recovered(scope):
        if scope in lost_seen:
            return False
        # An unknown scope (None) never recovers: the recorder adds only known publisher keys to `healthy_seen`.
        return bool(healthy_seen) if scope == shadow.ALL_PUBLISHERS else scope in healthy_seen
    still = frozenset(lost_seen) | frozenset(scope for scope in in_loss if not recovered(scope))
    reason = ('flush-lost' if flush_lost else 'version-change' if versions_changed else
              'publication-loss' if still and not in_loss else 'publication-recovered' if in_loss and not still else None)
    return reason, still


class ShadowWindows:
    """The current window and the rule for closing it. Has identity and state, so an object."""

    def __init__(self, recorder=None, connect=get_db):
        self._recorder = recorder
        self._connect = connect
        self._window = None
        self._versions = None
        self._in_loss = frozenset()
        self._flush_lost = False

    @property
    def recorder(self):
        return self._recorder or shadow.recorder

    async def start(self, versions, at):
        # Drained BEFORE the window opens: what was recorded until now belongs to no window, and everything recorded
        # from here on is flushed into the window this opens.
        self.recorder.take()
        self.recorder.evidence.take()
        db = await self._connect()
        try:
            await db.execute('BEGIN IMMEDIATE')
            await db.execute("UPDATE status_shadow_windows SET closed_at = flushed_at, close_reason = 'service-restart' "
                             "WHERE closed_at IS NULL")
            window = await self._open(db, versions, at)
            await db.commit()
            # Adopted at the commit, before the awaited close: a close that fails cannot leave the owner behind it.
            self._window, self._versions = window, versions
        finally:
            await db.close()

    async def tick(self, versions, at):
        """Flush what was recorded into the current window, then close it if this flush ends its continuity."""
        pending, lost_seen, healthy_seen = self.recorder.take()
        items, gaps = self.recorder.evidence.take()  # no await between: the items are the counts' disagreements
        reason, still_in_loss = next_reason(flush_lost=self._flush_lost, versions_changed=versions != self._versions,
                                            in_loss=self._in_loss, lost_seen=lost_seen, healthy_seen=healthy_seen)
        self._flush_lost = True  # until this flush commits: a failure from here on loses the counts just taken
        window = self._window
        db = await self._connect()
        try:
            await db.execute('BEGIN IMMEDIATE')
            # A version change is noticed after the counts were recorded, so they belong to the window being closed.
            for (owner, variant, site, name), count in pending.items():
                await db.execute(
                    'INSERT INTO status_shadow_window_counts (window_id, owner, variant, source, name, count) '
                    'VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (window_id, owner, variant, source, name) '
                    'DO UPDATE SET count = count + excluded.count', (window, owner, variant, site, name, count))
            await db.execute('UPDATE status_shadow_windows SET flushed_at = ? WHERE id = ?', (at, window))
            rows, size, opened_at = await evidence_store.append(db, window, items, gaps)
            if reason is None and evidence_store.rotation_due(rows, size, opened_at, at):
                reason = 'rotation'
            if reason:
                await db.execute('UPDATE status_shadow_windows SET closed_at = ?, close_reason = ? WHERE id = ?',
                                 (at, reason, window))
                window = await self._open(db, versions, at)
            await db.commit()
            # Adopted at the commit, before the awaited close: a close that fails cannot leave the owner behind it.
            self._window, self._versions, self._in_loss, self._flush_lost = window, versions, still_in_loss, False
        finally:
            await db.close()

    @staticmethod
    async def _open(db, versions, at):
        cursor = await db.execute(
            'INSERT INTO status_shadow_windows (comms_build, env_build, wrapper_build, opened_at, flushed_at, boot_id) '
            'VALUES (?, ?, ?, ?, ?, ?)', (versions.comms, versions.env, versions.wrapper, at, at, BOOT_ID))
        return cursor.lastrowid


async def recent_windows(limit=20):
    """The newest windows first, each with its counts as {owner: {variant: {site: {name: count}}}}."""
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
                'SELECT window_id, owner, variant, source, name, count FROM status_shadow_window_counts '
                f'WHERE window_id IN ({marks})', tuple(by_id))
            for window_id, owner, variant, site, name, count in await cursor.fetchall():
                by_id[window_id]['counts'].setdefault(owner, {}).setdefault(variant, {}).setdefault(site, {})[name] = count
        return windows
    finally:
        await db.close()


WINDOWS = ShadowWindows()


async def run_periodic(versions, windows=None, every=FLUSH_SECONDS):
    """The service's loop: open a window (retried until it opens), then flush every `every` seconds.

    A failure is logged and retried, never raised: this is a diagnostic, and a loop that ended on a transient error
    would stop recording without saying so. Only cancellation ends it.
    """
    windows = windows or WINDOWS
    while True:
        try:
            await windows.start(versions, clock.now())
            break
        except Exception as error:
            logger.warning('status shadow window could not open, retrying: %s', error)
            await asyncio.sleep(every)
    while True:
        await asyncio.sleep(every)
        try:
            await windows.tick(versions, clock.now())
        except Exception as error:
            logger.warning('status shadow window flush failed: %s', error)
        try:
            await expire_windows(clock.now())
        except Exception as error:
            logger.warning('status shadow window expiry failed: %s', error)


async def expire_windows(at):
    """Its own transaction, so an expiry that fails never costs a flush."""
    db = await get_db()
    try:
        return await evidence_store.expire(db, at)
    finally:
        await db.close()


async def stop(task):
    """Cancel the loop and wait for it, so shutdown continues whatever state the task ended in."""
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception as error:
        logger.warning('status shadow window task had failed: %s', error)
