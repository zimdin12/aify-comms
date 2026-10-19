"""C6's view owners beside today's: live counts, running-run promotion, worker readiness (status-switch S2c slice 5).

Four kinds of witness, kept apart: the pure candidates and C6's row precedence; the probe's delivery tags beside
today's unchanged answer; the readiness observer's inputs and its one bounded read; and the real sites, where today's
answer and its outcome do not move whatever the observer does.
"""
import asyncio
import contextlib
import json
import sqlite3
import unittest
from unittest import mock

from service.api_core import channel_delivery as cd, comms_view as cv, live_process_probes as probes
from service.api_core import partial_status_shadow as shadow, status_inputs, view_owners as vo
from service.api_core.delivery_turns import NOT_KNOWN as NK
from service.db import get_db
from service.tests._base import FastApiTestCase

U = lambda reason: (NK, reason)  # noqa: E731
NOW = '2026-10-19T00:10:00Z'
FRESH = '2026-10-19T00:09:30Z'
MACHINE = 'linux:test-host'


def rec(state='idle', process=('running', 'yes')):
    return shadow.Record('a', 'L', state, (), None, None, process)


class CandidateTest(unittest.TestCase):
    def test_live_counts_read_c6s_word_through_todays_function(self):
        cases = (
            ('idle is online', rec('idle'), None, False, (True, None)),
            ('working', rec('working'), None, False, (True, None)),
            ('stopped', rec('stopped'), None, False, (False, None)),
            ('misconfigured', rec('misconfigured'), None, False, (False, None)),
            ('a state with no word', rec('nonsense'), None, False, U('unknown-state')),
            ('row 3: stale', None, 'stale', False, (False, None)),
            ('a conflict carries the conflict_age answer', None, 'ambiguous-association', False, (False, None)),
            ('rows 2/2b: no record (D8 gap)', None, 'missing-row', False, U('no-record')),
            ('a lost view', None, 'unknown-loss', False, U('unknown-loss')),
            ('row 1 possible over a live answer', rec('idle'), None, True, U('operator-stop')),
            ('row 1 cannot change an all-stale conflict', None, 'ambiguous-association', True, (False, None)),
            ('row 1 cannot change a non-live answer', None, 'stale', True, (False, None)),
            ('row 1 possible over an unknown', None, 'missing-row', True, U('operator-stop')),
        )
        for name, record, reason, stopped, expected in cases:
            with self.subTest(name):
                self.assertEqual(vo.live_candidate(record, reason, stopped, (False, None)), expected)

    def test_promotion_is_row_5_after_every_row_above_it(self):
        for state in ('idle', 'shell', 'starting', 'available'):
            with self.subTest(state):
                self.assertEqual(vo.promotion_candidate(rec(state), None, True, False), (True, None))
        for state in ('working', 'blocked', 'stopped', 'misconfigured', 'unknown', 'offline'):
            with self.subTest(state):
                self.assertEqual(vo.promotion_candidate(rec(state), None, True, False), (False, None),
                                 'a usable fresh state row 5 does not name is False, not unknown')
        for reason in ('stale', 'missing-row', 'ambiguous-association'):
            with self.subTest(reason):
                self.assertEqual(vo.promotion_candidate(None, reason, True, False), (False, None))
        self.assertEqual(vo.promotion_candidate(None, 'unknown-knowledge', True, False), U('unknown-knowledge'))
        self.assertEqual(vo.promotion_candidate(rec('idle'), None, False, True), (False, None), 'no running run')
        self.assertEqual(vo.promotion_candidate(rec('idle'), None, True, True), U('operator-stop'))
        self.assertEqual(vo.promotion_candidate(rec('working'), None, True, True), (False, None))

    def test_process_is_running_and_verified(self):
        cases = ((('running', 'yes'), (True, None)), (('running', 'no'), (False, None)),
                 (('running', 'unknown'), U('process')), (('unknown', 'unknown'), U('process')),
                 (('starting', 'yes'), (False, None)), (('exited', 'no'), (False, None)), (('none', 'no'), (False, None)))
        for process, expected in cases:
            with self.subTest(process=repr(process)):
                self.assertEqual(vo.process_answer(rec(process=process), None), expected)
        self.assertEqual(vo.process_answer(None, 'missing-row'), U('no-record'))
        self.assertEqual(vo.process_answer(None, 'stale'), U('stale'))

    def test_delivery_and_the_and_table(self):
        self.assertEqual(vo.delivery_answer(None, None), (True, None), 'no component delivers by itself')
        self.assertEqual(vo.delivery_answer('sidecar', True), (True, None))
        self.assertEqual(vo.delivery_answer('sidecar', False), (False, None))
        self.assertEqual(vo.delivery_answer('sidecar', probes.PROBE_ERROR), U('delivery'))
        self.assertEqual(vo.delivery_answer('wrapper-child', 'unread'), U('delivery'))
        T, F = (True, None), (False, None)
        self.assertEqual(vo.readiness_candidate(T, T), T)
        self.assertEqual(vo.readiness_candidate(U('process'), F), F, 'a known undeliverable worker is not ready')
        self.assertEqual(vo.readiness_candidate(F, U('delivery')), F)
        self.assertEqual(vo.readiness_candidate(T, U('delivery')), U('delivery'))
        self.assertEqual(vo.readiness_candidate(U('stale'), U('delivery')), U('stale'), 'process names it first')


class RecordForTest(unittest.TestCase):
    """compare() now reads record_for(); its word and reasons are unchanged, and the raw record is the same row."""

    def test_the_word_comparison_and_the_raw_record_agree(self):
        mirror = shadow.Mirror()
        mirror.feed(shadow.Projection(('m', 'i'), 'snapshot', 1, FRESH, (rec('idle'),), ()))
        binding = shadow.Binding(mirror.view(), NOW)
        self.assertEqual(shadow.compare(binding, ('m', 'a')), ('online', None))
        self.assertEqual(shadow.record_for(binding, ('m', 'a')), (rec('idle'), None))
        odd = shadow.Mirror()
        odd.feed(shadow.Projection(('m', 'i'), 'snapshot', 1, FRESH, (rec('nonsense'),), ()))
        self.assertEqual(shadow.compare(shadow.Binding(odd.view(), NOW), ('m', 'a')), (None, 'unknown-state'))
        self.assertEqual(shadow.record_for(shadow.Binding(odd.view(), NOW), ('m', 'b')), (None, 'missing-row'))
        old = shadow.Mirror()
        old.feed(shadow.Projection(('m', 'i'), 'snapshot', 1, '2026-10-19T00:00:00Z', (rec('idle'),), ()))
        self.assertEqual(shadow.record_for(shadow.Binding(old.view(), NOW), ('m', 'a')), (None, 'stale'))


class PrepareTest(unittest.TestCase):
    def test_the_projection_carries_the_records_process(self):
        from service.tests.test_agent_state_receiver import AT, body
        projection = shadow.prepare(body(), AT)
        self.assertEqual(projection.rows[0].process, ('running', 'yes'))


class _Shadow:
    """A fresh recorder and mirror, a fixed shadow clock, and a record helper for one agent."""

    def setUp(self):
        super().setUp()
        self.recorder, self.mirror = shadow.Recorder(), shadow.Mirror()
        for p in (mock.patch.object(shadow, 'recorder', self.recorder), mock.patch.object(shadow, 'mirror', self.mirror),
                  mock.patch.object(shadow, 'clock', return_value=NOW)):
            p.start(); self.addCleanup(p.stop)

    def publish(self, *records, machine=MACHINE, applied=FRESH):
        self.mirror.feed(shadow.Projection((machine, 'i1'), 'snapshot', 1, applied, records, ()))

    def owner(self, name):
        return self.recorder.report()['byPurpose'][name]['variants']['-']


STALE_A, STALE_B, FRESH_B = '2026-10-19T00:00:00Z', '2026-10-19T00:01:00Z', '2026-10-19T00:09:50Z'


class ConflictAgeTest(_Shadow, unittest.TestCase):
    """Two publishers on the agent's machine hold it: rows 3 and 4, through the real Mirror, bind and observer."""

    ROW = {'id': 'a', 'machine_id': MACHINE, 'status': 'online'}

    def feed(self, instance, applied, *records, kind='snapshot', machine=MACHINE, generation=1):
        self.mirror.feed(shadow.Projection((machine, instance), kind, generation, applied,
                                           records or (rec('idle'),), ()))

    def observe(self, live=True, row=None):
        vo.observe_live_count(row or self.ROW, live, 'gate', 'online' if live else 'offline')
        return self.owner(vo.LIVE_COUNTS)

    def test_an_all_stale_conflict_is_row_3_offline(self):
        self.feed('i1', STALE_A); self.feed('i2', STALE_B)
        self.assertEqual(shadow.compare(shadow.bind(), (MACHINE, 'a')), (None, 'ambiguous-association'),
                         'the word comparison is unchanged: a conflict before any freshness')
        self.observe(live=False)
        report = self.observe(live=True)
        self.assertEqual((report['counts']['partialAgreed'], report['disagreementsByKey']),
                         (1, [dict(site='gate', today='true', candidate='false', count=1)]),
                         'offline today agrees; online today disagrees')

    def test_a_fresh_conflict_is_row_4_unknown_and_live(self):
        self.feed('i1', FRESH); self.feed('i2', FRESH_B)
        self.assertEqual(self.observe(live=True)['counts']['partialAgreed'], 1)

    def test_a_mixed_conflict_is_a_disclosed_gap(self):
        self.feed('i1', STALE_A); self.feed('i2', FRESH)
        self.assertEqual(self.observe()['unavailableByReason'], {'gate': {'mixed-age-conflict': 1}})

    def test_a_holder_it_cannot_read_names_why(self):
        cases = (('lost', lambda: self.mirror.invalidate((MACHINE, 'i2')), 'unknown-loss'),
                 ('incomplete', lambda: self.feed('i2', FRESH, kind='delta', generation=2), 'unknown-knowledge'),
                 ('unavailable', lambda: self.feed('i2', FRESH, kind='unavailable'), 'publisher-unavailable'))
        for name, fault, reason in cases:
            with self.subTest(name):
                self.mirror._view = shadow.MappingProxyType({})
                self.feed('i1', STALE_A); self.feed('i2', STALE_B)
                fault()
                answer = vo.conflict_age(shadow.bind(), (MACHINE, 'a'))
                self.assertEqual(answer, U(reason))

    def test_a_publisher_that_might_hold_a_third_record_withholds_it_but_another_machine_does_not(self):
        cases = (('lost', lambda: self.mirror.invalidate((MACHINE, 'i3'))),
                 ('incomplete', lambda: self.feed('i3', FRESH, shadow.Record('other', 'L', 'idle'), kind='delta',
                                                  generation=2)),
                 ('unavailable', lambda: self.feed('i3', FRESH, kind='unavailable')))
        for name, fault in cases:
            with self.subTest(name):
                self.mirror._view = shadow.MappingProxyType({})
                self.feed('i1', STALE_A); self.feed('i2', STALE_B)
                self.feed('i3', FRESH, shadow.Record('other', 'L', 'idle'))
                fault()
                self.assertEqual(vo.conflict_age(shadow.bind(), (MACHINE, 'a')), U('record-knowledge'))
        self.mirror._view = shadow.MappingProxyType({})
        self.feed('i1', STALE_A); self.feed('i2', STALE_B)
        self.feed('x1', FRESH, kind='delta', machine='win32:elsewhere')
        self.assertEqual(vo.conflict_age(shadow.bind(), (MACHINE, 'a')), (False, None),
                         'a publisher on another machine says nothing about this association')

    def test_each_predicate_is_asked_of_the_whole_population_in_order(self):
        def publisher(applied, *, loss=False, complete=True, unavailable=False, holds=True):
            rows = (rec('idle'),) if holds else (shadow.Record('other', 'L', 'idle'),)
            return shadow.Publisher(1, applied, complete, unavailable, rows, loss)

        def answer(*publishers, at=NOW):
            for order in (publishers, tuple(reversed(publishers))):
                view = shadow.MappingProxyType({(MACHINE, f'i{n}'): p for n, p in enumerate(order)})
                yield vo.conflict_age(shadow.Binding(view, at), (MACHINE, 'a'))
        cases = (
            ('holder loss before incomplete',
             (publisher(STALE_A, complete=False), publisher(STALE_B, loss=True)), U('unknown-loss')),
            ('incomplete before unavailable',
             (publisher(STALE_A, unavailable=True), publisher(STALE_B, complete=False)), U('unknown-knowledge')),
            ('a holder before a non-holder',
             (publisher(STALE_A, unavailable=True), publisher(FRESH, loss=True, holds=False), publisher(STALE_B)),
             U('publisher-unavailable')),
            ('a non-holder before a clock',
             (publisher('not-a-time'), publisher(STALE_B), publisher(FRESH, complete=False, holds=False)),
             U('record-knowledge')),
            ('invalid before future',
             (publisher('2026-10-19T01:00:00Z'), publisher('not-a-time')), U('invalid-clock')),
            ('a future clock', (publisher('2026-10-19T01:00:00Z'), publisher(STALE_B)), U('future')),
        )
        for name, publishers, expected in cases:
            with self.subTest(name):
                self.assertEqual(set(answer(*publishers)), {expected}, 'the same answer in either insertion order')

    def test_row_1_masks_only_what_it_could_change(self):
        stopped = dict(self.ROW, status='stopped')
        self.feed('i1', FRESH); self.feed('i2', FRESH_B)
        self.observe(live=False, row=stopped)
        self.mirror._view = shadow.MappingProxyType({})
        self.feed('i1', STALE_A); self.feed('i2', STALE_B)
        report = self.observe(live=False, row=stopped)
        self.assertEqual((report['unavailableByReason'], report['counts']['partialAgreed']),
                         ({'gate': {'operator-stop': 1}}, 1), 'a fresh conflict is withheld; a stale one stays False')

    def test_one_bind_serves_the_record_and_the_conflict(self):
        self.feed('i1', STALE_A); self.feed('i2', STALE_B)
        with mock.patch.object(shadow, 'bind', wraps=shadow.bind) as bind:
            vo.observe_live_count(self.ROW, False, 'gate', 'offline')
        self.assertEqual(bind.call_count, 1)


class ReadinessObserverTest(_Shadow, unittest.TestCase):
    ROW = {'id': 'a', 'machine_id': MACHINE, 'status': 'online'}

    def observe(self, liveness, answer=False, fail=False, during=None):
        asked = []

        async def probe(db, agent_id):
            asked.append(agent_id)
            if during:
                during()
            if fail:
                raise RuntimeError('bridge table unreadable')
            return answer
        with mock.patch.dict(vo.PROBES, {'sidecar': probe, 'wrapper-child': probe}):
            asyncio.run(vo.observe_readiness(None, self.ROW, liveness, 'legacy'))
        return asked

    def test_an_unread_component_is_asked_once_and_only_where_it_can_change_the_answer(self):
        self.publish(rec(process=('running', 'yes')))
        unread = cd._WorkerLiveness(True, False, False, ('wrapper-child', 'unread'))
        self.assertEqual(self.observe(unread, answer=True), ['a'])
        self.assertEqual(self.observe(unread, fail=True), ['a'], 'an error is asked once, then unknown')
        self.publish(rec(process=('exited', 'no')))
        self.assertEqual(self.observe(unread), [], 'process False reads nothing')
        readiness = self.owner(vo.READINESS)
        self.assertEqual((readiness['counts']['partialAgreed'], readiness['counts']['partialDisagreed']), (1, 1))
        self.assertEqual(readiness['unavailableByReason'], {'legacy': {'delivery': 1}})

    def test_a_read_today_made_is_reused_and_an_error_is_never_asked_again(self):
        self.publish(rec(process=('running', 'yes')))
        for state in (True, False, probes.PROBE_ERROR):
            with self.subTest(state=repr(state)):
                self.assertEqual(self.observe(cd._WorkerLiveness(state is True, False, False, ('sidecar', state))), [])
        self.assertEqual(self.owner(vo.READINESS)['unavailableByReason'], {'legacy': {'delivery': 1}})

    def test_the_record_is_frozen_before_the_read(self):
        self.publish(rec(process=('running', 'yes')))
        self.observe(cd._WorkerLiveness(True, False, False, ('sidecar', 'unread')), answer=True,
                     during=lambda: self.publish(rec(process=('exited', 'no'))))
        self.assertEqual(self.owner(vo.READINESS)['counts']['partialAgreed'], 1, 'the frozen running process, read')

    def test_today_promotes_only_a_word_that_changes(self):
        self.publish(rec('working'))
        running = {'activeRun': {'status': 'running'}}
        vo.observe_promotion(self.ROW, 'working', 'working', running, 'serialize')
        vo.observe_promotion(self.ROW, 'online', 'working', running, 'serialize')
        promotion = self.owner(vo.PROMOTION)
        self.assertEqual((promotion['counts']['partialAgreed'], promotion['disagreementsByKey']),
                         (1, [dict(site='serialize', today='true', candidate='false', count=1)]),
                         'working stays working: no promotion today; online to working is one')

    def test_no_component_and_no_association(self):
        self.publish(rec(process=('running', 'yes')))
        self.observe(cd._WorkerLiveness(True, False, False, (None, None)))
        asyncio.run(vo.observe_readiness(None, None, cd._WorkerLiveness(True, False, False), 'legacy'))
        readiness = self.owner(vo.READINESS)
        self.assertEqual((readiness['counts']['partialAgreed'], readiness['unavailableByReason']),
                         (1, {'legacy': {'association': 1}}))


class _Db(FastApiTestCase):
    def _exec(self, query, params=()):
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(query, params); conn.commit()
        finally:
            conn.close()

    def _row(self, agent_id):
        conn = sqlite3.connect(self._db_path); conn.row_factory = sqlite3.Row
        try:
            return conn.execute('SELECT * FROM agents WHERE id = ?', (agent_id,)).fetchone()
        finally:
            conn.close()

    def _register(self, agent_id, **fields):
        body = {'agentId': agent_id, 'role': 'coder', 'machineId': MACHINE, **fields}
        answer = self.client.post('/api/v1/agents', json=body)
        self.assertEqual(answer.status_code, 200, answer.text)

    def _bridge(self, agent_id, kind, seconds_ago=5):
        self._exec("INSERT INTO bridge_instances (id, agent_id, bridge_kind, registered_at, last_seen) "
                   "VALUES (?, ?, ?, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), "
                   "strftime('%Y-%m-%dT%H:%M:%SZ', 'now', ?))",
                   (f'{kind}-{agent_id}', agent_id, kind, f'-{seconds_ago} seconds'))

    def _terminal(self, agent_id, command):
        self._exec("INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, runtime, status, command, "
                   "created_at, updated_at) VALUES (?, ?, ?, 'env', 'codex', 'running', ?, "
                   "strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))",
                   (f'term-{agent_id}', f'sess-{agent_id}', agent_id, command))

    def _liveness(self, agent_id, mode='managed', live_session=False, wrap=None):
        async def run():
            db = await get_db()
            try:
                return await cd._worker_liveness_for(wrap(db) if wrap else db, self._row(agent_id),
                                                     agent_session_mode=mode, live_session=live_session)
            finally:
                await db.close()
        return asyncio.run(run())


class DeliveryTagTest(_Db):
    """The probe's delivery tag, beside today's unchanged answer and reason flags."""

    def test_sidecar_runtimes_always_read_the_sidecar_and_never_fold_the_console_into_it(self):
        self._register('cl-a', runtime='claude-code', sessionMode='managed')
        self._bridge('cl-a', 'channel-sidecar')
        self.assertEqual(self._liveness('cl-a'), cd._WorkerLiveness(False, False, True, ('sidecar', True)),
                         'a live sidecar with no console: not live, no_console, delivery True')
        self._register('cl-b', runtime='claude-code', sessionMode='managed')
        self.assertEqual(self._liveness('cl-b'), cd._WorkerLiveness(False, True, False, ('sidecar', False)))
        self._register('cl-c', runtime='claude-code', sessionMode='managed')
        self._terminal('cl-c', 'claude-aify')
        self.assertEqual(self._liveness('cl-c'), cd._WorkerLiveness(False, True, False, ('sidecar', False)),
                         'a live console with a dead sidecar: not live, no_sidecar, delivery False')

    def test_a_read_that_raised_is_an_error_not_an_absence_and_today_is_unchanged(self):
        self._register('cl-e', runtime='claude-code', sessionMode='managed')

        class Failing:
            def __init__(self, db):
                self.db = db

            def __getattr__(self, name):
                return getattr(self.db, name)

            def execute(self, sql, *args):
                if 'channel-sidecar' in sql:
                    raise RuntimeError('bridge table unreadable')
                return self.db.execute(sql, *args)
        self.assertEqual(self._liveness('cl-e', wrap=Failing),
                         cd._WorkerLiveness(False, True, False, ('sidecar', probes.PROBE_ERROR)))

        async def booleans():
            db = await get_db()
            try:
                broken = Failing(db)
                broken.execute = mock.Mock(side_effect=RuntimeError('unreadable'))
                return (await probes._has_live_channel_sidecar(broken, 'cl-e'),
                        await probes._has_live_managed_wrapper_child(broken, 'cl-e'),
                        await probes.wrapper_child_state(broken, 'cl-e'))
            finally:
                await db.close()
        self.assertEqual(asyncio.run(booleans()), (False, False, probes.PROBE_ERROR),
                         'every existing caller still reads an error as False')

    def test_a_terminal_that_proved_the_worker_leaves_the_component_unread_by_todays_precedence(self):
        self._register('cx-t', runtime='codex', sessionMode='managed')
        self._terminal('cx-t', 'codex-aify')
        self.assertEqual(self._liveness('cx-t', live_session=True),
                         cd._WorkerLiveness(True, False, False, ('wrapper-child', 'unread')))
        self._register('cx-f', runtime='codex', sessionMode='managed', runtimeConfig={'channelEnabled': True})
        self._terminal('cx-f', 'codex-aify')
        self.assertEqual(self._liveness('cx-f', live_session=True).delivery, ('sidecar', 'unread'),
                         'the flag branch comes before codex')

    def test_without_a_terminal_the_branch_reads_its_component(self):
        self._register('cx-c', runtime='codex', sessionMode='managed')
        self._bridge('cx-c', 'managed-wrapper-child')
        self.assertEqual(self._liveness('cx-c'), cd._WorkerLiveness(True, False, False, ('wrapper-child', True)))
        self._register('cx-n', runtime='codex', sessionMode='managed')
        self.assertEqual(self._liveness('cx-n'), cd._WorkerLiveness(False, False, False, ('wrapper-child', False)))

    def test_the_flag_branch_reads_the_sidecar_and_a_child_error_is_not_live(self):
        self._register('cx-flag', runtime='codex', sessionMode='managed', runtimeConfig={'channelEnabled': True})
        self.assertEqual(self._liveness('cx-flag'), cd._WorkerLiveness(False, True, False, ('sidecar', False)))
        self._register('cx-err', runtime='codex', sessionMode='managed')
        self._bridge('cx-err', 'managed-wrapper-child')

        class Failing:
            def __init__(self, db):
                self.db = db

            def __getattr__(self, name):
                return getattr(self.db, name)

            def execute(self, sql, *args):
                if 'managed-wrapper-child' in sql:
                    raise RuntimeError('bridge table unreadable')
                return self.db.execute(sql, *args)
        self.assertEqual(self._liveness('cx-err', wrap=Failing),
                         cd._WorkerLiveness(False, False, False, ('wrapper-child', probes.PROBE_ERROR)),
                         'a fresh child row exists, but the read raised: not live, as today')

    def test_a_resident_names_no_component(self):
        self._register('res', runtime='codex', sessionMode='resident')
        self.assertEqual(self._liveness('res', mode='resident', live_session=True).delivery, (None, None))


class SitesTest(_Shadow, _Db):
    """The real sites: each observes, and today's outcome is the same whether the observer works, fails or records
    nothing."""

    def setUp(self):
        super().setUp()
        self._register('m-agent', runtime='claude-code', sessionMode='managed')
        self._register('r-agent', runtime='codex', sessionMode='resident', sessionHandle='thread-r')
        self.publish(shadow.Record('m-agent', 'L', 'idle', (), None, None, ('running', 'yes')),
                     shadow.Record('r-agent', 'L', 'idle', (), None, None, ('running', 'yes')))

    def sites(self, owner):
        report = self.recorder.report()['byPurpose'][owner]['variants']['-']
        return {site: row['observed'] for site, row in (report.get('bySite') or {}).items()}

    def reasons(self, owner):
        return self.owner(owner).get('unavailableByReason') or {}

    def roster(self):
        answer = self.client.get('/api/v1/agents')
        self.assertEqual(answer.status_code, 200, answer.text)
        return {aid: (a['status'], a.get('statusNote')) for aid, a in answer.json()['agents'].items()}

    def test_both_gate_callers_hand_over_their_row(self):
        self.roster()
        one = self.client.get('/api/v1/agents/m-agent')
        self.assertEqual(one.status_code, 200, one.text)
        self.assertGreaterEqual(self.sites(vo.LIVE_COUNTS).get('gate', 0), 3)
        self.assertNotIn('association', self.reasons(vo.LIVE_COUNTS).get('gate', {}))

    def test_every_gate_branch_is_observed_and_an_absent_association_is_unknown(self):
        from service.api_core.registration_gates import _enforce_env_reachable_gate

        async def gate(payload, **kw):
            db = await get_db()
            try:
                return await _enforce_env_reachable_gate(payload, db, {}, 'm-agent', **kw)
            finally:
                await db.close()
        row = self._row('m-agent')
        for payload in ({'status': 'offline'}, {'status': 'online', 'sessionMode': 'resident'},
                        {'status': 'online', 'sessionMode': 'managed'}):
            with self.subTest(payload=repr(payload)):
                self.assertEqual(asyncio.run(gate(dict(payload), association_row=row))['status'], payload['status'])
        asyncio.run(gate({'status': 'online', 'sessionMode': 'resident'}))
        self.assertEqual(self.sites(vo.LIVE_COUNTS)['gate'], 4)
        self.assertEqual(self.reasons(vo.LIVE_COUNTS)['gate'], {'association': 1})
        live_counts = self.owner(vo.LIVE_COUNTS)
        self.assertEqual((live_counts['counts']['partialAgreed'], live_counts['disagreementsByKey']),
                         (2, [dict(site='gate', today='false', candidate='true', count=1)]),
                         'the offline payload beside an idle record; the two online payloads agree')
        [item], _ = self.recorder.evidence.take()
        self.assertEqual(json.loads(item.payload)['comms'], dict(
            held='offline', live=False, association=dict(id='m-agent', machineId=row['machine_id'], status=row['status'])))

    def test_the_analytics_counts_are_observed(self):
        calls = []
        real = vo.observe_live_count
        with mock.patch.object(vo, 'observe_live_count', side_effect=lambda *a: (calls.append(a), real(*a))):
            for path in ('/api/v1/analytics', '/api/v1/analytics/pulse'):
                answer = self.client.get(path)
                self.assertEqual(answer.status_code, 200, answer.text)
        from service.status_engine import is_live_agent_status
        self.assertEqual(sorted((site, live, is_live_agent_status(held)) for _, live, site, held in calls),
                         [('series', False, False), ('series', True, True), ('summary', False, False),
                          ('summary', True, True)], 'each site hands over the status its own answer came from')
        self.assertEqual({s: n >= 2 for s, n in self.sites(vo.LIVE_COUNTS).items() if s != 'gate'},
                         {'summary': True, 'series': True})

    def test_promotion_is_observed_where_today_promotes(self):
        self._exec("INSERT INTO dispatch_runs (id, from_agent, target_agent, dispatch_mode, execution_mode, "
                   "message_type, subject, body, priority, status, requested_at, started_at) VALUES "
                   "('run-p', 'dashboard', 'm-agent', 'start_if_possible', 'managed', 'info', 's', 'b', 'normal', "
                   "'running', datetime('now'), datetime('now'))")
        self.roster()
        answer = self.client.post('/api/v1/messages/send', json={
            'from_agent': 'dashboard', 'to': 'm-agent', 'type': 'info', 'subject': 's', 'body': 'b', 'trigger': True})
        self.assertEqual(answer.status_code, 200, answer.text)
        sites = self.sites(vo.PROMOTION)
        self.assertGreaterEqual(sites.get('serialize', 0), 2)
        self.assertGreaterEqual(sites.get('preflight', 0), 1)
        self.assertGreaterEqual(self.owner(vo.PROMOTION)['counts']['partialCompared'], 1)

    def test_the_buffer_full_path_is_observed(self):
        from service.api_core import dispatch_runs
        self._exec("INSERT INTO dispatch_runs (id, from_agent, target_agent, dispatch_mode, execution_mode, "
                   "message_type, subject, body, priority, status, queue_if_busy, requested_at) VALUES "
                   "('run-q', 'dashboard', 'm-agent', 'start_if_possible', 'managed', 'info', 's', 'b', 'normal', "
                   "'queued', 1, datetime('now'))")
        with mock.patch.object(dispatch_runs, '_append_pending_dispatch_body', return_value=None):
            answer = self.client.post('/api/v1/messages/send', json={
                'from_agent': 'dashboard', 'to': 'm-agent', 'type': 'info', 'subject': 's2', 'body': 'b2',
                'trigger': True, 'queueIfBusy': True})
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertIn('buffer-full', self.sites(vo.PROMOTION), answer.json())

    def test_readiness_is_observed_at_its_three_sites(self):
        from service.api_core.dispatch_start import twin_refusal
        from service.api_core.status_inputs import _compute_live_status_cache, _gather_status_inputs

        async def run():
            db = await get_db()
            try:
                await _gather_status_inputs(db, self._row('m-agent'))
                await _compute_live_status_cache(db, self._row('m-agent'))
                return await twin_refusal(db, self._row('r-agent'))
            finally:
                await db.close()
        asyncio.run(run())
        self.assertEqual({s: n >= 1 for s, n in self.sites(vo.READINESS).items()},
                         {'engine': True, 'legacy': True, 'twin': True})

    def test_a_failing_observer_changes_no_site_outcome(self):
        from service.api_core.dispatch_start import twin_refusal
        before = self.roster()
        for fault in (mock.patch.object(vo, 'live_candidate', side_effect=RuntimeError('broken')),
                      mock.patch.object(vo, 'promotion_candidate', side_effect=RuntimeError('broken')),
                      mock.patch.object(vo, 'readiness_candidate', side_effect=RuntimeError('broken'))):
            fault.start(); self.addCleanup(fault.stop)
        self.assertEqual(self.roster(), before)
        for path in ('/api/v1/analytics', '/api/v1/analytics/pulse'):
            self.assertEqual(self.client.get(path).status_code, 200)

        async def twin():
            db = await get_db()
            try:
                return await twin_refusal(db, self._row('r-agent'))
            finally:
                await db.close()
        self.assertEqual(asyncio.run(twin()), '')
        self.assertIn('shadow-error', self.reasons(vo.LIVE_COUNTS).get('gate', {}))
        self.assertIn('shadow-error', self.reasons(vo.READINESS).get('twin', {}))
        with mock.patch.object(self.recorder, 'record_owner', side_effect=RuntimeError('recorder broken')):
            self.assertEqual(self.roster(), before)
            self.assertEqual(asyncio.run(twin()), '')


ARMS = ('healthy', 'candidate-fault', 'recorder-fault')


class SiteOutcomeTest(_Shadow, _Db):
    """Per site, today's outcome under each arm: the observer healthy, every candidate raising, and the recorder
    raising. Each arm that writes runs on its own fresh, equivalent agent; each expectation is fixed by the fixture."""

    @contextlib.contextmanager
    def arm(self, name):
        """One arm's patches, stopped even when the site raises (so no fault leaks into the next arm); a fault arm
        must be shown reached, since a fault nobody hit proves nothing."""
        if name == 'candidate-fault':
            patches = [mock.patch.object(vo, fn, side_effect=RuntimeError('candidate broken'))
                       for fn in ('live_candidate', 'promotion_candidate', 'readiness_candidate')]
        elif name == 'recorder-fault':
            patches = [mock.patch.object(self.recorder, 'record_owner', side_effect=RuntimeError('recorder broken'))]
        else:
            patches = []
        mocks = [p.start() for p in patches]
        try:
            yield
        finally:
            for p in patches:
                p.stop()
        if patches:
            self.assertTrue(any(m.called for m in mocks), 'the fault was never reached')

    def managed(self, agent_id, *, worker=False):
        self._register(agent_id, runtime='claude-code', sessionMode='managed')
        self.publish(*[shadow.Record(a, 'L', 'idle', (), None, None, ('running', 'yes'))
                       for a in self.published + [agent_id]])
        self.published.append(agent_id)
        if worker:
            self._bridge(agent_id, 'channel-sidecar')
            self._terminal(agent_id, 'claude-aify')

    def setUp(self):
        super().setUp()
        self.published = []

    roster = SitesTest.roster

    def run_db(self, coro_fn):
        async def run():
            db = await get_db()
            try:
                return await coro_fn(db)
            finally:
                await db.close()
        return asyncio.run(run())

    def test_the_analytics_counts(self):
        self.managed('an-avail')
        self._register('an-off', runtime='codex', sessionMode='resident', sessionHandle='t')
        self._register('an-stop', runtime='codex', sessionMode='managed')
        self._exec("UPDATE agents SET status = 'stopped' WHERE id = 'an-stop'")
        self.assertEqual({a: s for a, (s, _) in self.roster().items()},
                         {'an-avail': 'available', 'an-off': 'offline', 'an-stop': 'stopped'},
                         'the fixture: one live status (available) beside two non-live ones')
        for name in ARMS:
            with self.subTest(name):
                with self.arm(name):
                    counts = [self.client.get(path).json()['onlineAgents']
                              for path in ('/api/v1/analytics', '/api/v1/analytics/pulse')]
                self.assertEqual(counts, [1, 1])

    def test_the_send_preflight(self):
        for name in ARMS:
            with self.subTest(name):
                agent = f'pf-{name}'
                self.managed(agent)
                with self.arm(name):
                    answer = self.client.post('/api/v1/messages/send', json={
                        'from_agent': 'dashboard', 'to': agent, 'type': 'info', 'subject': 's', 'body': 'b', 'trigger': True})
                self.assertEqual(answer.status_code, 200, answer.text)
                body = answer.json()
                decision = [(h['targetAgentId'], h['reason'].split(':')[0]) for h in body.get('notStarted') or []]
                self.assertEqual((body['ok'], body['stored'], body['recipientStatus'][agent]['status'],
                                  body['recipientStatus'][agent]['unread'], decision, self._rows_of(agent)),
                                 (True, True, 'available', 1,
                                  [(agent, 'Cannot start managed claude-code for this agent')], []),
                                 'an undefined managed agent: stored in its inbox, the wake refused, no run')

    def _rows_of(self, agent):
        conn = sqlite3.connect(self._db_path)
        try:
            return conn.execute('SELECT id, status, body FROM dispatch_runs WHERE target_agent = ? ORDER BY id',
                                (agent,)).fetchall()
        finally:
            conn.close()

    def test_the_buffer_full_rejection(self):
        from service.api_core import dispatch_runs
        for name in ARMS:
            with self.subTest(name):
                agent = f'bf-{name}'
                self.managed(agent)
                self._exec("INSERT INTO dispatch_runs (id, from_agent, target_agent, dispatch_mode, execution_mode, "
                           "message_type, subject, body, priority, status, queue_if_busy, requested_at) VALUES "
                           "(?, 'dashboard', ?, 'start_if_possible', 'managed', 'info', 's', 'first', 'normal', "
                           "'queued', 1, datetime('now'))", (f'run-{agent}', agent))
                with self.arm(name):
                    with mock.patch.object(dispatch_runs, '_append_pending_dispatch_body', return_value=None):
                        answer = self.client.post('/api/v1/messages/send', json={
                            'from_agent': 'dashboard', 'to': agent, 'type': 'info', 'subject': 's2', 'body': 'second',
                            'trigger': True, 'queueIfBusy': True})
                self.assertEqual(answer.status_code, 200, answer.text)
                refused = [h for h in answer.json().get('notStarted') or [] if h.get('targetAgentId') == agent]
                self.assertEqual([(h['reason'], h['recipientStatus'], h['hasActiveRun'], h['bufferedCount'])
                                  for h in refused], [('buffer_full', 'available', False, 1)], answer.json())
                self.assertEqual(self._rows_of(agent), [(f'run-{agent}', 'queued', 'first')],
                                 'the buffered run is kept unchanged and nothing new is stored')

    def test_the_engine_and_legacy_consume_the_worker_result(self):
        for name in ARMS:
            for worker in (True, False):
                with self.subTest(arm=name, worker=str(worker)):
                    agent = f'wk-{name}-{worker}'.lower()
                    self.managed(agent, worker=worker)
                    with self.arm(name):
                        inputs, cache = self.run_db(lambda db: asyncio.gather(
                            status_inputs._gather_status_inputs(db, self._row(agent)),
                            status_inputs._compute_live_status_cache(db, self._row(agent))))
                    self.assertEqual(inputs.worker_present, worker)
                    self.assertEqual(cache['status'], 'online' if worker else 'available')

    def test_the_twin_refusal_engages_and_stays_quiet(self):
        from service.api_core.dispatch_start import twin_refusal
        for name in ARMS:
            for live in (True, False):
                with self.subTest(arm=name, live=str(live)):
                    agent = f'tw-{name}-{live}'.lower()
                    self._register(agent, runtime='codex', sessionMode='resident', sessionHandle=f't-{agent}')
                    if live:
                        self._exec("INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, status, "
                                   "started_at, last_seen) VALUES (?, ?, 'env', 'codex', 'running', "
                                   "strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))",
                                   (f's-{agent}', agent))
                    with self.arm(name):
                        refusal = self.run_db(lambda db: twin_refusal(db, self._row(agent)))
                    self.assertEqual(bool(refusal), live, refusal)


class ReadinessCompositionTest(_Shadow, _Db):
    """The legacy site's composition on this test's own DB: today's `_worker_liveness_for`, then the observer with the
    production probes. Attempts and the matching SELECTs are counted apart, for today and for the observer."""

    def setUp(self):
        super().setUp()
        self.attempts = []
        real = dict(vo.PROBES)

        def spy(component):
            async def probe(db, agent_id):
                self.attempts.append(component)
                return await real[component](db, agent_id)
            return probe
        p = mock.patch.dict(vo.PROBES, {c: spy(c) for c in real})
        p.start(); self.addCleanup(p.stop)

    def compose(self, agent, *, live_session, kind='managed-wrapper-child', fail=False):
        """(today's delivery tag, today's matching SELECTs, observer attempts, observer's added matching SELECTs) for the
        component whose bridge kind is `kind`."""
        selects = []

        class Counting:
            def __init__(self, db):
                self.db = db

            def __getattr__(self, name):
                return getattr(self.db, name)

            def execute(self, sql, *args):
                if kind in sql:
                    selects.append(1)
                    if fail:
                        raise RuntimeError('bridge table unreadable')
                return self.db.execute(sql, *args)

        async def run():
            db = await get_db()
            try:
                counted = Counting(db)
                liveness = await cd._worker_liveness_for(counted, self._row(agent), agent_session_mode='managed',
                                                         live_session=live_session)
                today = len(selects)
                await vo.observe_readiness(counted, self._row(agent), liveness, 'legacy')
                return liveness, today, len(selects) - today
            finally:
                await db.close()
        self.attempts.clear()
        liveness, today, added = asyncio.run(run())
        return liveness.delivery, today, len(self.attempts), added

    def codex(self, agent, process, *, terminal=False, child=False):
        self._register(agent, runtime='codex', sessionMode='managed')
        self.publish(shadow.Record(agent, 'L', 'idle', (), None, None, process))
        if terminal:
            self._terminal(agent, 'codex-aify')
        if child:
            self._bridge(agent, 'managed-wrapper-child')

    def test_an_unread_child_costs_one_attempt_and_one_select(self):
        self.codex('co-unread', ('running', 'yes'), terminal=True, child=True)
        self.assertEqual(self.compose('co-unread', live_session=True), (('wrapper-child', 'unread'), 0, 1, 1))
        self.assertEqual(self.owner(vo.READINESS)['counts']['partialAgreed'], 1, 'the child the shadow read is live')

    def test_a_decisive_process_false_reads_nothing(self):
        self.codex('co-exited', ('exited', 'no'), terminal=True, child=True)
        self.assertEqual(self.compose('co-exited', live_session=True), (('wrapper-child', 'unread'), 0, 0, 0))

    def test_a_child_today_read_or_failed_is_never_read_again(self):
        self.codex('co-read', ('running', 'yes'), child=True)
        self.assertEqual(self.compose('co-read', live_session=False), (('wrapper-child', True), 1, 0, 0))
        self.codex('co-err', ('running', 'yes'), child=True)
        self.assertEqual(self.compose('co-err', live_session=False, fail=True),
                         (('wrapper-child', probes.PROBE_ERROR), 1, 0, 0))
        self.assertEqual(self.owner(vo.READINESS)['unavailableByReason'], {'legacy': {'delivery': 1}})


    def flagged(self, agent, process, *, terminal=False, sidecar=False):
        """A managed agent with the channel flag: its branch's component is the sidecar (the flag precedes codex)."""
        self._register(agent, runtime='codex', sessionMode='managed', runtimeConfig={'channelEnabled': True})
        self.publish(shadow.Record(agent, 'L', 'idle', (), None, None, process))
        if terminal:
            self._terminal(agent, 'codex-aify')
        if sidecar:
            self._bridge(agent, 'channel-sidecar')

    def test_an_unread_sidecar_costs_one_attempt_and_one_select(self):
        self.flagged('fl-unread', ('running', 'yes'), terminal=True, sidecar=True)
        self.assertEqual(self.compose('fl-unread', live_session=True, kind='channel-sidecar'),
                         (('sidecar', 'unread'), 0, 1, 1))
        self.assertEqual(self.attempts, ['sidecar'])
        self.assertEqual(self.owner(vo.READINESS)['counts']['partialAgreed'], 1, 'the sidecar the shadow read is live')

    def test_a_decisive_process_false_reads_no_sidecar(self):
        self.flagged('fl-exited', ('exited', 'no'), terminal=True, sidecar=True)
        self.assertEqual(self.compose('fl-exited', live_session=True, kind='channel-sidecar'),
                         (('sidecar', 'unread'), 0, 0, 0))
        self.assertEqual(self.owner(vo.READINESS)['disagreementsByKey'],
                         [dict(site='legacy', today='true', candidate='false', count=1)])

    def test_a_sidecar_today_read_or_failed_is_never_read_again(self):
        self.flagged('fl-read', ('running', 'yes'), sidecar=True)
        self.assertEqual(self.compose('fl-read', live_session=False, kind='channel-sidecar'),
                         (('sidecar', True), 1, 0, 0))
        self.flagged('fl-err', ('running', 'yes'), sidecar=True)
        self.assertEqual(self.compose('fl-err', live_session=False, kind='channel-sidecar', fail=True),
                         (('sidecar', probes.PROBE_ERROR), 1, 0, 0))
        readiness = self.owner(vo.READINESS)
        self.assertEqual((readiness['counts']['partialAgreed'], readiness['unavailableByReason']),
                         (1, {'legacy': {'delivery': 1}}))
