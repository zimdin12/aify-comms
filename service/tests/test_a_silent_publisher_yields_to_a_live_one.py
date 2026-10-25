"""Task #5: silence yields only under same-machine, same-agent live coverage.

Existing report-interval tests cover the clock math. These cases cover selection,
restart contact provenance, and the actual status reader/cache. No daemon runs.
"""
import asyncio
from contextlib import closing
from dataclasses import replace
import sqlite3
from unittest.mock import patch

from service.api_core import mirror_restore, operational_view as ov, partial_status_shadow as shadow
from service.db import get_db
from service.tests._base import FastApiTestCase
from service.tests.test_agent_state_receiver import body, wire, AT
from service.tests.test_report_interval_is_aify_envs import at, idle
from service.tests.write_fence_race import dump

MACHINE, AGENT = 'test-host', 'alpha'
OLD, LIVE = (MACHINE, 'old'), (MACHINE, 'live')


class ASilentPublisherYieldsToALiveOne(FastApiTestCase):
    def setUp(self):
        super().setUp()
        for p in (patch.object(shadow, 'mirror', shadow.Mirror()),
                  patch.object(shadow, '_quarantined', False)):
            p.start()
            self.addCleanup(p.stop)

    def post(self, instance, seconds, *, kind='snapshot', every=20_000, **fields):
        value = body(kind, machineId=MACHINE, instance=instance, **{'agents': [idle()], **fields})
        if every is not None:
            value['reportEveryMs'] = every
        if kind == 'unavailable':
            value.pop('agents', None)
        # Every clock reads the publication's moment: the ingest now re-derives the words it expired, through the view's clock.
        with patch('service.clock.now', return_value=at(seconds)), patch.object(shadow, 'clock', return_value=at(seconds)):
            response = self.client.post('/api/v1/agent-state', content=wire(value),
                                        headers={'content-type': 'application/json'})
        self.assertEqual(response.status_code, 204, response.text)
        return value

    def view(self, seconds, *, agent=AGENT, view=None):
        binding = shadow.Binding(shadow.mirror.view() if view is None else view, at(seconds))
        holders = ov.holders_of(binding, MACHINE, agent)
        return holders, ov.comms_view(holders, ov.Messaging())

    def restart(self):
        shadow.mirror = shadow.Mirror()
        return asyncio.run(mirror_restore.restore_mirror(get_db))

    def test_silence_yields_per_agent_at_its_own_limit_without_deleting_the_key(self):
        self.post('old', 0, agents=[idle(), idle('only-old')])
        self.post('live', 59, every=100_000, generation=1)
        before = dump(self._db_path)
        holders, v = self.view(59)
        self.assertEqual(len(holders.holders), 2, 'two reporting publishers really remain ambiguous')
        self.assertEqual(v.status, 'unknown')
        holders, v = self.view(60)
        self.assertEqual(([h.availability for h in holders.holders], v.status, v.eligibility),
                         (['fresh'], 'online', 'known'))
        self.assertEqual(ov.claim_decision(v, False), 'proceed')
        self.assertEqual(self.view(60, agent='only-old')[1].eligibility, 'unknown', 'coverage is per agent')
        self.assertEqual(dump(self._db_path), before, 'yield is a read, not database cleanup')
        self.assertIn(OLD, shadow.mirror.view(), 'the publisher and its cursor are retained')
        self.post('old', 61, publication=2)
        self.assertEqual(len(self.view(61)[0].holders), 2, 'an advancing old key may report again')
        self.assertEqual(self.view(61)[1].status, 'unknown', 'renewal restores genuine ambiguity')

    def test_restored_silence_survives_restart_but_restored_data_never_becomes_fresh(self):
        self.post('old', 0)
        self.assertEqual(self.restart().restored, (OLD,))
        self.assertEqual(self.view(61)[1].eligibility, 'unknown', 'dead alone is never authority')
        self.post('live', 61, every=100_000)
        holders, v = self.view(61)
        self.assertEqual((len(holders.holders), v.status, v.eligibility), (1, 'online', 'known'))
        self.assertTrue(shadow.mirror.view()[OLD].restored)
        self.assertEqual(shadow.mirror.view()[OLD].report, (AT, 20_000))
        self.restart()
        self.assertEqual(self.view(62)[1].eligibility, 'unknown', 'neither restored key is a live replacement')
        self.post('live', 62, every=100_000, publication=2)
        self.assertEqual(self.view(62)[1].status, 'online', 'no database deletion or publisher retirement needed')

    def test_missing_or_unreadable_contact_proof_cannot_yield(self):
        self.post('old', 0)
        self.post('live', 61, every=100_000)
        current = dict(shadow.mirror.view())
        self.assertEqual(self.view(61, view=current)[1].status, 'online', 'positive coverage control')
        for report in (None, (AT, None), (AT, True), ('bad-clock', 20_000), (at(62), 20_000)):
            with self.subTest(report=report):
                changed = {**current, OLD: replace(current[OLD], report=report, restored=True)}
                self.assertEqual(self.view(61, view=changed)[1].eligibility, 'unknown')
        for peer in (replace(current[LIVE], complete=False), replace(current[LIVE], restored=True),
                     replace(current[LIVE], unavailable=True), replace(current[LIVE], loss=True),
                     replace(current[LIVE], report=None), replace(current[LIVE], report=(AT, 1000)),
                     replace(current[LIVE], rows=())):
            with self.subTest(peer=repr(peer)):
                self.assertEqual(self.view(61, view={OLD: current[OLD], LIVE: peer})[1].eligibility, 'unknown')
        foreign = {OLD: current[OLD], ('other-machine', 'live'): current[LIVE]}
        self.assertEqual(self.view(61, view=foreign)[1].eligibility, 'unknown', 'another host cannot cover it')
        lost = shadow.Binding(current, at(61), loss=True)
        self.assertEqual(ov.holders_of(lost, MACHINE, AGENT), ov.Holders((), True))

    def test_a_fresh_peer_must_list_this_agent_to_cover_its_stale_holder(self):
        self.post('old', 0)
        for publication, agents in enumerate(([], [idle('another-agent')]), 1):
            with self.subTest(agents=agents):
                self.post('live', 61, agents=agents, publication=publication)
                holders, v = self.view(61)
                self.assertEqual([(h.availability, h.applied_at) for h in holders.holders], [('stale', AT)])
                self.assertEqual(v.last_known, ('online', AT), 'unrelated fresh contact cannot erase this record')

    def test_a_stale_empty_publisher_is_unreadable_unless_covered(self):
        self.post('old', 0, agents=[])
        uncovered = dict(shadow.mirror.view())
        self.post('live', 61)
        holders, v = self.view(61)
        self.assertFalse(holders.unreadable, 'the silent empty peer yields under real coverage')
        self.assertEqual((v.status, v.eligibility), ('online', 'known'))
        holders, v = self.view(61, view=uncovered)
        self.assertEqual(holders.holders, ())
        self.assertTrue(holders.unreadable, 'without coverage the non-fresh empty publisher is unreadable')
        self.assertEqual((v.status, v.last_known, v.eligibility, v.busy), ('unknown', None, 'unknown', None))

    def test_only_an_advancing_accepted_report_changes_the_persisted_contact(self):
        first = self.post('old', 0)
        for value, reason in ((first, 'duplicate'), ({**first, 'generation': 9}, 'stale'),
                              ({**first, 'inputs': {'operatorStop': 'tracked'}}, 'conflict')):
            with self.subTest(reason=reason), patch('service.clock.now', return_value=at(59)):
                response = self.client.post('/api/v1/agent-state', content=wire(value))
                self.assertEqual(response.json(), {'applied': False, 'reason': reason})
        self.restart()
        self.assertEqual(shadow.mirror.view()[OLD].report, (AT, 20_000))
        self.post('old', 59, kind='unavailable', publication=2, every=100_000)
        self.restart()
        p = shadow.mirror.view()[OLD]
        self.assertEqual((p.report, p.data_applied_at, p.unavailable), ((at(59), 100_000), AT, True))
        self.post('old', 60, kind='changes', publication=3, every=None)
        self.restart()
        self.assertEqual(shadow.mirror.view()[OLD].report, (at(60), None), 'withdrawal is also durable')

    def test_unknown_cached_status_rechecks_when_an_unreadable_peer_misses_its_limit(self):
        from service.tests.frozen_clock import frozen_service_clock
        with frozen_service_clock(AT):
            response = self.client.post('/api/v1/agents', json={
                'agentId': AGENT, 'role': 'coder', 'machineId': MACHINE})
        self.assertEqual(response.status_code, 200, response.text)
        self.post('old', 0, kind='unavailable', every=20_000)
        self.post('live', 59, every=100_000)
        for seconds, expected in ((59, 'unknown'), (60, 'online')):
            with self.subTest(seconds=seconds), frozen_service_clock(at(seconds)), \
                    patch.object(shadow, 'clock', return_value=at(seconds)):
                answer = self.client.get(f'/api/v1/agents/{AGENT}')
                self.assertEqual(answer.status_code, 200, answer.text)
                self.assertEqual(answer.json()['agent']['status'], expected)

    def test_a_prechange_database_migrates_without_inventing_a_legacy_interval(self):
        from service.db import init_db
        self.post('old', 0)
        with closing(sqlite3.connect(self._db_path)) as db:
            for name in ('report_received_at', 'report_every_ms'):
                columns = {r[1] for r in db.execute('PRAGMA table_info(agent_state_shadow_publishers)')}
                if name in columns:
                    db.execute(f'ALTER TABLE agent_state_shadow_publishers DROP COLUMN {name}')
            db.commit()
        asyncio.run(init_db(self._db_path))
        self.restart()
        self.post('live', 61, every=100_000)
        self.assertEqual(self.view(61)[1].eligibility, 'unknown', 'missing historic cadence cannot be guessed')
        with closing(sqlite3.connect(self._db_path)) as db:
            saved = db.execute('SELECT report_received_at, report_every_ms FROM agent_state_shadow_publishers '
                               'WHERE machine_id=? AND instance=?', OLD).fetchone()
        self.assertEqual(saved, (None, None))
