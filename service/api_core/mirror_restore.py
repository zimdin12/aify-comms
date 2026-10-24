"""Restart restore (0.9.8 hard switch, SW1): the status mirror reloaded from the saved shadow tables at service start.

The mirror is process-local; the tables are not. Without a load, every publisher reads absent after a restart, and an
absent publisher cannot make an agent unknown. With one, every non-retired publisher comes back as last-known data that
is never fresh:

  - Each publisher is `restored`, never fresh until its own accepted complete snapshot. Saved contact proves silence,
    not fresh data: after three announced intervals it may yield where a live peer lists the same agent (task #5).
    Completeness is not saved and is never inferred: a snapshot followed by changes and two
    changes publications leave the same rows, so the rows cannot say which history they came from.
  - A retired publisher stays absent. An unavailable one stays unavailable. A row or publisher the ingest validator
    refuses makes that publisher lost (unknown), never a failed start.
  - The data generation, incarnation, publication and application stamps are the saved ones. The cursor stays in its
    table, where the ingest orders against it.

The load holds the ingest lock and installs no key an ingest already applied. It runs in the lifespan before routes
are served, with no bootstrap timer.
"""
import json
from dataclasses import dataclass

from service.api_core import agent_state_shadow as ingest
from service.api_core import partial_status_shadow as shadow


@dataclass(frozen=True)
class RestoreResult:
    """What the load put into the mirror, by publisher key."""
    restored: tuple = ()
    lost: tuple = ()
    retired: tuple = ()
    skipped: tuple = ()  # already applied by an ingest, so left as it is
    unreadable: bool = False  # the tables could not be read: the mirror is quarantined


def restored_record(record_json, origin):
    """One saved row as the mirror holds it, validated exactly as an ingest validates a published record."""
    record = json.loads(record_json)
    ingest._record(record)
    return shadow.record_of(record, origin)


def restored_publisher(publisher, rows):
    """A saved publisher and its rows as a restored mirror entry. Raises when any row is not a valid record."""
    generation, incarnation = publisher['data_generation'], publisher['data_incarnation_id']
    # A row's own publication is not saved; its generation and incarnation are its publisher's data generation's.
    records = tuple(restored_record(row['record_json'], (generation, incarnation, None, row['row_applied_at'], None))
                    for row in rows)
    return shadow.Publisher(generation, publisher['data_applied_at'], False, publisher['kind'] == 'unavailable',
                            records, False, incarnation, publisher['data_publication'], None, True,
                            saved_report(publisher))


def saved_report(publisher):
    """Historical contact, not fresh data. A legacy row has neither receipt nor announced cadence."""
    if 'report_received_at' not in publisher.keys() or not publisher['report_received_at']:
        return None
    return publisher['report_received_at'], publisher['report_every_ms']


def lost_publisher(publisher):
    """A saved publisher whose rows cannot be read: lost, with no rows, keeping its saved coordinates."""
    return shadow.Publisher(publisher['data_generation'], publisher['data_applied_at'], False, False, (), True,
                            publisher['data_incarnation_id'], publisher['data_publication'], None, True,
                            saved_report(publisher))


async def restore_mirror(connect):
    """Load every saved publisher into the mirror, under the ingest lock. `connect` opens a database connection."""
    async with ingest._INGEST_LOCK:
        try:
            publishers, rows = await _saved_tables(connect)
        except Exception:
            shadow.quarantine()
            return RestoreResult(unreadable=True)
        restored, lost, retired, skipped = [], [], [], []
        for publisher in publishers:
            key = (publisher['machine_id'], publisher['instance'])
            if publisher['kind'] == 'retired':
                retired.append(key)
                continue
            try:
                entry, bucket = restored_publisher(publisher, rows.get(key, ())), restored
            except Exception:
                entry, bucket = lost_publisher(publisher), lost
            (bucket if shadow.mirror.restore(key, entry) else skipped).append(key)
        return RestoreResult(tuple(restored), tuple(lost), tuple(retired), tuple(skipped))


async def _saved_tables(connect):
    db = await connect()
    try:
        publishers = await (await db.execute('SELECT * FROM agent_state_shadow_publishers')).fetchall()
        rows = {}
        for row in await (await db.execute('SELECT * FROM agent_state_shadow_rows')).fetchall():
            rows.setdefault((row['machine_id'], row['instance']), []).append(row)
        return publishers, rows
    finally:
        await db.close()
