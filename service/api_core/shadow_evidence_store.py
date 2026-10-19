"""Where the status shadow's evidence persists (S2c-E E1): appended in the window flush, rotated, expired, read.

APPEND is inside the tick's transaction, so a batch's counts, items and gaps commit or fail together. The window's
flushed_seq is the FRONTIER: every item at or below it is committed. A window holds at most ROTATE_ROWS + one batch
and ROTATE_BYTES + one batch: the tick tests the totals AFTER appending, and a window past either, or ROTATE_AGE old,
closes 'rotation' at that tick, so the next window starts empty and every batch lands whole in the window its
evaluations were recorded in.

EXPIRY deletes a closed window older than RETAIN with everything under it, and leaves a TOMBSTONE carrying its
range, versions and totals, so an expired window answers 'expired' rather than looking like no history.

A READ is one page of one window, bound to the frontier its first page saw, with each owner's COVERAGE: disagreed =
complete items + incomplete items + gaps, and complete only for an evidence owner whose partition holds with
nothing incomplete or missing, in a window that closed cleanly. Which owners are evidence owners is the window's own
set, frozen when it opened (E2): an owner outside it is counts-only, and missing or malformed metadata is 'unknown'.
None of the three is ever complete. An open window's read is provisional, and so is a
read bound below the window's own frontier: the totals then count items the read does not enumerate.
"""
from datetime import datetime, timedelta
import json

from service.api_core.shadow_evidence import BOOT_ID
from service.api_core.shadow_provenance import (
    UNKNOWN_BUILD,
    as_json,
    env_coherent,
    fold_unverified,
    parse_builds,
    parse_owners,
)
from service.db import E1_EVIDENCE_OWNERS
from service.api_core.shadow_owners import OWNERS, variants_of

ROTATE_ROWS = 48_000
ROTATE_BYTES = 60 * 1024 * 1024
ROTATE_AGE = timedelta(hours=24)
RETAIN = timedelta(days=14)
PAGE_LIMIT = 200
#: A window that ended for one of these kept every flushed batch; flush-lost, service-restart and an open window did not.
CLEAN_CLOSES = ('version-change', 'publication-loss', 'publication-recovered', 'rotation')


def _parse(at):
    return datetime.fromisoformat(at.replace('Z', '+00:00'))


def rotation_due(rows, size, opened_at, at):
    """PURE. Whether the window, holding these totals after this tick's append, closes at this tick."""
    return rows > ROTATE_ROWS or size > ROTATE_BYTES or _parse(at) - _parse(opened_at) >= ROTATE_AGE


async def append(db, window, items, gaps):
    """Inside the caller's transaction. The window's resulting (rows, bytes, opened_at)."""
    for i in items:
        await db.execute(
            'INSERT INTO status_shadow_evidence (window_id, boot_id, seq, owner, variant, site, agent_id, eval_at, '
            'complete, payload) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (window, BOOT_ID, i.seq, i.owner, i.variant, i.site, i.agent_id,
             i.eval_at, int(i.complete), i.payload))
    for (owner, variant, site, reason), count in gaps.items():
        await db.execute(
            'INSERT INTO status_shadow_evidence_gaps (window_id, owner, variant, site, reason, count) '
            'VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (window_id, owner, variant, site, reason) '
            'DO UPDATE SET count = count + excluded.count', (window, owner, variant, site, reason, count))
    await db.execute(
        'UPDATE status_shadow_windows SET flushed_seq = MAX(flushed_seq, ?), evidence_rows = evidence_rows + ?, '
        'evidence_bytes = evidence_bytes + ? WHERE id = ?',
        (max((i.seq for i in items), default=0), len(items), sum(len(i.payload) for i in items), window))
    row = await (await db.execute(
        'SELECT evidence_rows, evidence_bytes, opened_at FROM status_shadow_windows WHERE id = ?', (window,))).fetchone()
    return row[0], row[1], row[2]


async def add_provenance(db, window, builds, fold):
    """Inside the caller's transaction. Union this batch's env builds and fold readers into the window; returns the
    builds the window held before. A record that cannot be read is never repaired: unreadable builds become 'unknown'
    in the union, and an unreadable fold record is left exactly as stored, so it keeps refusing every fold owner."""
    row = await (await db.execute(
        'SELECT env_builds, fold_unverified FROM status_shadow_windows WHERE id = ?', (window,))).fetchone()
    seen = parse_builds(row[0]) if row else None
    seen = frozenset((UNKNOWN_BUILD,)) if seen is None else seen
    readers = parse_owners(row[1]) if row else None
    stored_fold = row[1] if readers is None and row else as_json((readers or frozenset()) | fold)
    await db.execute('UPDATE status_shadow_windows SET env_builds = ?, fold_unverified = ? WHERE id = ?',
                     (as_json(seen | builds), stored_fold, window))
    return seen


async def _totals(db, window):
    """{(owner, variant, site): {disagreed, completeRows, incompleteRows, missing: {reason: n}}}, all committed."""
    totals = {}

    def entry(key):
        return totals.setdefault(tuple(key), dict(disagreed=0, completeRows=0, incompleteRows=0, missing={}))
    for *key, count in await (await db.execute(
            "SELECT owner, variant, source, count FROM status_shadow_window_counts "
            "WHERE window_id = ? AND name = 'partialDisagreed'", (window,))).fetchall():
        entry(key)['disagreed'] = count
    for *key, complete, count in await (await db.execute(
            'SELECT owner, variant, site, complete, COUNT(*) FROM status_shadow_evidence WHERE window_id = ? '
            'GROUP BY owner, variant, site, complete', (window,))).fetchall():
        entry(key)['completeRows' if complete else 'incompleteRows'] = count
    for *key, reason, count in await (await db.execute(
            'SELECT owner, variant, site, reason, count FROM status_shadow_evidence_gaps WHERE window_id = ?',
            (window,))).fetchall():
        entry(key)['missing'][reason] = count
    return totals


NOT_INSTRUMENTED, UNKNOWN = 'not-instrumented', 'unknown'


def window_owners(instrumented, stored):
    """PURE. The evidence owners a window opened with: a frozenset, NOT_INSTRUMENTED when it recorded no evidence, or
    UNKNOWN when its stored set is missing or malformed. Never the running process's set."""
    if not instrumented:
        return NOT_INSTRUMENTED
    try:
        owners = json.loads(stored)
    except (TypeError, ValueError):
        return UNKNOWN
    if not isinstance(owners, list) or not all(isinstance(o, str) and o in OWNERS for o in owners):
        return UNKNOWN
    return frozenset(owners)


def instrumentation(owners, owner):
    if owners in (NOT_INSTRUMENTED, UNKNOWN):
        return owners
    return 'evidence' if owner in owners else 'counts-only'


def coverage(totals, owners, clean, coherent=True, fold=frozenset()):
    """PURE. Every owner and variant, each with its sites' partition and whether that site's evidence is complete.
    `owners` is window_owners(...) for the window; `clean` whether it closed keeping every batch; `coherent` whether
    it saw exactly one known env build; `fold` the owners whose fold reads it recorded (B6a). Each refusal is named in
    the owner's `gaps`, and an owner with a gap is never complete."""
    rows = []
    for owner in OWNERS:
        level = instrumentation(owners, owner)
        gaps = [gap for gap, present in (('env-version', not coherent), ('fold-context', owner in fold)) if present]
        evidence = level == 'evidence' and not gaps
        for variant in variants_of(owner):
            sites = {}
            for (o, v, site), t in sorted(totals.items()):
                if (o, v) != (owner, variant):
                    continue
                holds = t['disagreed'] == t['completeRows'] + t['incompleteRows'] + sum(t['missing'].values())
                sites[site] = dict(t, partitionHolds=holds, complete=evidence and clean and holds and
                                   t['incompleteRows'] == 0 and not t['missing'])
            rows.append(dict(owner=owner, variant=variant, instrumentation=level, gaps=gaps, sites=sites))
    return rows


def _window(row):
    keys = ('id', 'commsBuild', 'envBuild', 'wrapperBuild', 'openedAt', 'flushedAt', 'closedAt', 'closeReason',
            'flushedSeq', 'evidenceRows', 'evidenceBytes', 'bootId', 'evidenceOwners', 'envBuilds', 'foldUnverified')
    return dict(zip(keys, row))


async def read_page(db, window_id, after=0, limit=PAGE_LIMIT, frontier=None, owner=None, variant=None, site=None):
    """One page of a window's items in seq order, bound to `frontier` (the first page's flushed_seq), with the window's
    coverage, all in one read transaction. None for a window that never existed."""
    limit = max(1, min(int(limit), PAGE_LIMIT))
    await db.execute('BEGIN')
    try:
        row = await (await db.execute(
            'SELECT id, comms_build, env_build, wrapper_build, opened_at, flushed_at, closed_at, close_reason, '
            'flushed_seq, evidence_rows, evidence_bytes, boot_id, evidence_owners, env_builds, fold_unverified '
            'FROM status_shadow_windows WHERE id = ?',
            (window_id,))).fetchone()
        if row is None:
            return await _expired(db, window_id)
        window = _window(row)
        bound = window['flushedSeq'] if frontier is None else min(int(frontier), window['flushedSeq'])
        filters, params = '', [window_id, after, bound]
        for column, value in (('owner', owner), ('variant', variant), ('site', site)):
            if value is not None:
                filters += f' AND {column} = ?'
                params.append(value)
        items = await (await db.execute(
            'SELECT seq, payload FROM status_shadow_evidence WHERE window_id = ? AND seq > ? AND seq <= ?'
            f'{filters} ORDER BY seq LIMIT ?', (*params, limit + 1))).fetchall()
        totals = await _totals(db, window_id)
    finally:
        await db.rollback()
    page = items[:limit]
    # The totals are the window's at its own flushed_seq. A read bound below it enumerates fewer items than they
    # count, so it is never final: provisional, nothing complete, and the reader restarts at windowFrontier.
    stale = bound < window['flushedSeq']
    return dict(window=dict(window, expired=False, provisional=window['closedAt'] is None or stale),
                frontier=bound, windowFrontier=window['flushedSeq'], staleFrontier=stale,
                items=[json.loads(payload) for _, payload in page],
                nextAfter=page[-1][0] if page else after, hasMore=len(items) > limit,
                coverage=coverage(totals, window_owners(window['bootId'] is not None, window['evidenceOwners']),
                                  window['closeReason'] in CLEAN_CLOSES and not stale,
                                  env_coherent(window['envBuilds']), fold_unverified(window['foldUnverified'])))


async def _expired(db, window_id):
    row = await (await db.execute(
        'SELECT window_id, comms_build, wrapper_build, opened_at, closed_at, close_reason, summary, deleted_at '
        'FROM status_shadow_window_tombstones WHERE window_id = ?', (window_id,))).fetchone()
    if row is None:
        return None
    summary = json.loads(row[6])
    # Every tombstone written before E2 is an E1 window: one without the key that was instrumented had E1's owners.
    stored = summary.get('evidenceOwners', E1_EVIDENCE_OWNERS if summary['instrumented'] else None)
    return dict(window=dict(id=row[0], commsBuild=row[1], envBuild=summary['envBuild'], wrapperBuild=row[2],
                            openedAt=row[3], flushedAt=summary['flushedAt'], closedAt=row[4], closeReason=row[5],
                            flushedSeq=summary['flushedSeq'], evidenceRows=summary['evidenceRows'],
                            evidenceBytes=summary['evidenceBytes'], bootId=summary['bootId'],
                            evidenceOwners=stored, envBuilds=summary.get('envBuilds'),
                            foldUnverified=summary.get('foldUnverified'), deletedAt=row[7], expired=True,
                            provisional=False),
                frontier=summary['flushedSeq'], windowFrontier=summary['flushedSeq'], staleFrontier=False, items=[],
                nextAfter=0, hasMore=False,
                coverage=coverage({tuple(t['key']): t['totals'] for t in summary['totals']},
                                  window_owners(summary['instrumented'], stored), False,
                                  env_coherent(summary.get('envBuilds')),
                                  fold_unverified(summary.get('foldUnverified'))))


async def expire(db, at):
    """Delete every closed window older than RETAIN, each leaving its tombstone, in one transaction. The number
    deleted. Never an open window."""
    cutoff = _parse(at) - RETAIN

    async def old_windows():
        rows = await (await db.execute(
            'SELECT id, comms_build, wrapper_build, opened_at, closed_at, close_reason, flushed_seq, evidence_rows, '
            'evidence_bytes, boot_id, env_build, flushed_at, evidence_owners, env_builds, fold_unverified '
            'FROM status_shadow_windows WHERE closed_at IS NOT NULL')).fetchall()
        return [r for r in rows if _parse(r[4]) < cutoff]
    if not await old_windows():
        return 0  # the usual tick: no write lock taken
    await db.execute('BEGIN IMMEDIATE')
    try:
        old = await old_windows()
        for r in old:
            totals = await _totals(db, r[0])
            # Every field a live read shows, as the window held it: an unknown env build stays an explicit null.
            summary = dict(flushedSeq=r[6], evidenceRows=r[7], evidenceBytes=r[8], bootId=r[9], envBuild=r[10],
                           flushedAt=r[11], evidenceOwners=r[12], envBuilds=r[13], foldUnverified=r[14],
                           instrumented=r[9] is not None,
                           totals=[dict(key=list(k), totals=t) for k, t in sorted(totals.items())])
            await db.execute(
                'INSERT INTO status_shadow_window_tombstones (window_id, comms_build, wrapper_build, opened_at, '
                'closed_at, close_reason, summary, deleted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                (*r[:6], json.dumps(summary, sort_keys=True, separators=(',', ':')), at))
            for table in ('status_shadow_evidence', 'status_shadow_evidence_gaps', 'status_shadow_window_counts'):
                await db.execute(f'DELETE FROM {table} WHERE window_id = ?', (r[0],))
            await db.execute('DELETE FROM status_shadow_windows WHERE id = ?', (r[0],))
        await db.commit()
    except BaseException:
        await db.rollback()
        raise
    return len(old)
