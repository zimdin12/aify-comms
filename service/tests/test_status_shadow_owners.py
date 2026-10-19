"""The shadow recorder per C8 owner (status-switch S2c slice 1).

A non-word owner is counted under its own (owner, variant, site), so it never moves the status word's denominator, and
an owner nobody has measured says so rather than reading as zero disagreements.
"""
import asyncio
import sqlite3
import tempfile
from pathlib import Path

import aiosqlite

from service.tests._base import FastApiTestCase


class OwnerRecorderTest(FastApiTestCase):
    def setUp(self):
        super().setUp()
        from service.api_core import partial_status_shadow as shadow
        from service.api_core import shadow_owners, shadow_windows
        self.shadow, self.owners, self.windows = shadow, shadow_owners, shadow_windows
        self.recorder = shadow.Recorder()

    def test_busy_persists_under_busy_and_leaves_the_word_alone(self):
        owner = self.windows.ShadowWindows(self.recorder)
        versions = self.windows.Versions(comms='c1')
        asyncio.run(owner.start(versions, '2026-10-19T01:00:00Z'))
        self.recorder.record('refresh', 'online', 'online')
        self.recorder.record_owner('busy', 'strict', 'claim-gate', True, False)
        self.recorder.record_owner('busy', 'strict', 'claim-gate', True, None, reason='no-age')
        asyncio.run(owner.tick(versions, '2026-10-19T01:00:30Z'))
        [window] = asyncio.run(self.windows.recent_windows(1))
        self.assertEqual(window['counts']['busy'], {'strict': {'claim-gate': {
            'observed': 2, 'evaluated': 2, 'partialCompared': 1, 'partialDisagreed': 1, 'disagree:true:false': 1,
            'unavailable': 1, 'unavailable:no-age': 1}}})
        self.assertEqual(window['counts']['status word']['-']['refresh']['observed'], 1)
        report = self.recorder.report()['byPurpose']
        self.assertEqual(report['status word']['counts']['observed'], 1, 'the word counts only its own evaluations')
        busy = report['busy']
        self.assertTrue(busy['instrumented'])
        self.assertEqual(busy['variants']['strict']['counts']['partialDisagreed'], 1)
        self.assertEqual(busy['variants']['P-1'], self.owners.UNOBSERVED, 'a variant nobody measured says so')

    def test_an_unavailable_reason_stays_with_the_site_that_gave_it(self):
        def report(first, second):
            recorder = self.shadow.Recorder()
            recorder.record_owner('busy', 'strict', 'claim-gate', True, None, reason=first)
            recorder.record_owner('busy', 'strict', 'send-gate', True, None, reason=second)
            return recorder.report()['byPurpose']['busy']['variants']['strict']
        one, swapped = report('seeded', 'no-age'), report('no-age', 'seeded')
        self.assertEqual(one['unavailableByReason'], {'claim-gate': {'seeded': 1}, 'send-gate': {'no-age': 1}})
        self.assertNotEqual(one, swapped, 'two histories that differ only in which site gave which reason')
        self.assertEqual(one['counts'], swapped['counts'])

    def test_every_owner_and_variant_is_reported_and_an_unmeasured_one_says_so(self):
        report = self.recorder.report()
        self.assertEqual(report['schemaVersion'], 2)
        self.assertEqual(tuple(report['byPurpose']), self.owners.OWNERS)
        for owner in self.owners.OWNERS[1:]:
            row = report['byPurpose'][owner]
            self.assertEqual((row['instrumented'], row['comparisonScope']), (False, 'not-instrumented'))
            self.assertEqual(tuple(row['variants']), self.owners.variants_of(owner))
        self.assertEqual(tuple(report['byPurpose']['busy']['variants']), ('strict', 'P-1'))

    def test_a_wrong_owner_variant_or_site_is_refused(self):
        for owner, variant, site in (('status word', '-', 'x'), ('busy', '-', 'x'), ('claim', 'strict', 'x'),
                                     ('nobody', '-', 'x'), ('claim', '-', 'Has Space')):
            with self.subTest(owner=owner, variant=variant, site=site), self.assertRaises(ValueError):
                self.recorder.record_owner(owner, variant, site, True, True)

    def test_an_answer_outside_the_vocabulary_is_unavailable_never_a_comparison(self):
        self.recorder.record_owner('claim', '-', 'claim-gate', 1, True)
        self.recorder.record_owner('live counts', '-', 'roster', 'online', 'idle')
        claim = self.recorder.report()['byPurpose']['claim']['variants']['-']
        self.assertEqual((claim['counts']['partialCompared'], claim['unavailableByReason']), (0, {'claim-gate': {'invalid-answer': 1}}))
        counts = self.recorder.report()['byPurpose']['live counts']['variants']['-']['counts']
        self.assertEqual(counts['unavailable'], 1, "'idle' is aify-env's word, not a status word")


class CountsTableMigrationTest(FastApiTestCase):
    """A database from before `variant` keeps every count, under the no-variant `-`."""

    def test_an_old_counts_table_is_rebuilt_with_its_rows(self):
        from service import db as dbmod
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'old.db'
            old = sqlite3.connect(path)
            old.executescript(
                'CREATE TABLE status_shadow_windows (id INTEGER PRIMARY KEY AUTOINCREMENT, comms_build TEXT NOT NULL, '
                'env_build TEXT, wrapper_build TEXT, opened_at TEXT NOT NULL, flushed_at TEXT NOT NULL, closed_at TEXT, '
                'close_reason TEXT);'
                'CREATE TABLE status_shadow_window_counts (window_id INTEGER NOT NULL, owner TEXT NOT NULL, '
                'source TEXT NOT NULL, name TEXT NOT NULL, count INTEGER NOT NULL, '
                'PRIMARY KEY (window_id, owner, source, name), '
                'FOREIGN KEY (window_id) REFERENCES status_shadow_windows(id));'
                "INSERT INTO status_shadow_windows VALUES (1, 'c1', NULL, NULL, 't0', 't0', NULL, NULL);"
                "INSERT INTO status_shadow_window_counts VALUES (1, 'status word', 'refresh', 'observed', 7);")
            old.commit(); old.close()

            async def migrate_twice():
                async with aiosqlite.connect(path) as conn:
                    await dbmod._migrate_status_shadow_window_counts(conn)
                    await dbmod._migrate_status_shadow_window_counts(conn)  # a second start finds nothing to do
                    await conn.commit()
            asyncio.run(migrate_twice())
            new = sqlite3.connect(path)
            try:
                self.assertEqual(new.execute('SELECT window_id, owner, variant, source, name, count '
                                             'FROM status_shadow_window_counts').fetchall(),
                                 [(1, 'status word', '-', 'refresh', 'observed', 7)])
                key = [r[1] for r in sorted(new.execute('PRAGMA table_info(status_shadow_window_counts)'),
                                            key=lambda r: r[5]) if r[5]]
                self.assertEqual(key, ['window_id', 'owner', 'variant', 'source', 'name'])
                self.assertEqual(new.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE '%before_variant%'")
                                 .fetchone()[0], 0)
            finally:
                new.close()
