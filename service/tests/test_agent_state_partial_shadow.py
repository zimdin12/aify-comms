"""Partial shadow through accepted ingest and the actual status owners, offline only."""
import asyncio
import json
import sqlite3
from contextlib import closing
from unittest.mock import AsyncMock, Mock, patch

from service.tests._base import FastApiTestCase
from service.tests.test_agent_state_receiver import body, row, wire, AT
from service.status_engine import StatusInputs


def inputs():
    return StatusInputs(mode='managed', alive=True, in_turn=True, awaiting_input=False,
                        worker_present=True, env_reachable=True, disabled=False,
                        bridge_stale=False, has_live_session=True)


class PartialShadowTest(FastApiTestCase):
    def setUp(self):
        super().setUp()
        from service.api_core import partial_status_shadow as shadow
        self.shadow = shadow
        self.mirror = shadow.Mirror()
        self.recorder = shadow.Recorder()
        self.patches = [patch.object(shadow, 'mirror', self.mirror),
                        patch.object(shadow, 'recorder', self.recorder),
                        patch.object(shadow, 'clock', return_value=AT)]
        for p in self.patches:
            p.start(); self.addCleanup(p.stop)
        self.agent = {'id': 'alpha', 'machine_id': ' Host '}

    def apply(self, value, at=AT, factory=None):
        from service.api_core.agent_state_shadow import AgentStateShadowStore
        return asyncio.run(AgentStateShadowStore(factory).apply(wire(value), at))

    def counts(self, source=None):
        status = self.recorder.report()['byPurpose']['status word']
        return status['bySource'][source] if source else status['counts']

    def observe(self, actual='working', at=AT):
        with patch.object(self.shadow, 'clock', return_value=at):
            bound = self.shadow.bind()
        self.shadow.observe(bound, self.agent, inputs(), actual, 'refresh')

    def test_last_observations_are_named_detached_and_bounded(self):
        self.apply(body())
        from dataclasses import replace
        value = replace(inputs(), config_defect='secret configuration')
        bound = self.shadow.bind()
        self.shadow.observe(bound, self.agent, value, 'working', 'refresh')
        report = self.recorder.report()['byPurpose']['status word']
        last = report.get('lastBySource')
        self.assertIsNotNone(last, 'mandatory last redacted observations missing')
        observation = last['refresh']['partial-agree']
        self.assertEqual(observation['actual'], 'working')
        self.assertEqual(observation['candidate'], 'working')
        self.assertTrue(observation['inputs']['alive'])
        self.assertTrue(observation['inputs']['config_defect'])
        self.assertEqual(observation['inputs']['mode'], 'managed')
        object.__setattr__(value, 'alive', False)
        observation['inputs']['alive'] = False
        self.assertTrue(self.recorder.report()['byPurpose']['status word']['lastBySource']['refresh']['partial-agree']['inputs']['alive'])
        for _ in range(30): self.observe('online')
        self.shadow.observe(bound, self.agent, None, 'stopped', 'refresh')
        last = self.recorder.report()['byPurpose']['status word']['lastBySource']
        self.assertEqual(sum(len(row) for row in last.values()), 15)
        self.assertIsNone(last['refresh']['non-evaluation']['inputs'])
        self.assertEqual(last['refresh']['partial-disagree']['actual'], 'online')
        text = json.dumps(last)
        for secret in ('alpha', 'secret configuration', 'CaseSensitive', 'real-old'):
            self.assertNotIn(secret, text)

    def test_prepare_failure_preserves_acceptance_and_invalidates_only_after_commit(self):
        self.apply(body())
        old = self.mirror.view()
        with patch.object(self.shadow, 'prepare', side_effect=RuntimeError('private prepare')):
            self.assertFalse(self.apply(body()).applied)
            self.assertIs(self.mirror.view(), old)
            self.assertTrue(self.apply(body(publication=2)).applied)
        self.observe()
        self.assertEqual(self.counts()['unavailable'], 1)
        self.assertEqual(self.recorder.report()['byPurpose']['status word']['unavailableByReason']['unknown-loss'], 1)
        with closing(sqlite3.connect(self._db_path)) as db:
            self.assertEqual(db.execute('SELECT publication FROM agent_state_shadow_publishers').fetchone()[0], 2)

    def test_invalidation_failure_preserves_committed_result_without_fresh_lie(self):
        self.apply(body())
        with patch.object(self.mirror, 'feed', side_effect=RuntimeError('feed private')), \
             patch.object(self.mirror, 'invalidate', side_effect=RuntimeError('invalidate private')):
            self.assertTrue(self.apply(body(publication=2)).applied)
        self.observe()
        self.assertEqual(self.counts()['unavailable'], 1)
        self.assertEqual(self.recorder.report()['byPurpose']['status word']['unavailableByReason']['unknown-loss'], 1)
        with closing(sqlite3.connect(self._db_path)) as db:
            self.assertEqual(db.execute('SELECT publication FROM agent_state_shadow_publishers').fetchone()[0], 2)

    def test_concurrent_ingests_publish_in_actual_commit_order_before_close(self):
        from service.db import get_db
        from service.api_core.agent_state_shadow import AgentStateShadowStore
        events = []
        async def scenario():
            committed = asyncio.Event()
            release = asyncio.Event()
            class Connection:
                def __init__(inner, db, label): inner.db, inner.label = db, label
                async def execute(inner, *args): return await inner.db.execute(*args)
                async def rollback(inner): await inner.db.rollback()
                async def commit(inner):
                    await inner.db.commit()
                    events.append(('commit', inner.label))
                    if inner.label == 1:
                        committed.set()
                        await release.wait()
                async def close(inner):
                    events.append(('close', inner.label))
                    await inner.db.close()
            async def first(): return Connection(await get_db(), 1)
            async def second():
                events.append(('connect', 2))
                return Connection(await get_db(), 2)
            real_feed = self.mirror.feed
            def feed(p): events.append(('feed', p.rows[0].state)); real_feed(p)
            with patch.object(self.mirror, 'feed', side_effect=feed):
                a = asyncio.create_task(AgentStateShadowStore(first).apply(wire(body()), AT))
                await committed.wait()
                b = asyncio.create_task(AgentStateShadowStore(second).apply(wire(body(publication=2, agents=[dict(row(), state='idle')])), AT))
                await asyncio.sleep(0)
                self.assertNotIn(('connect', 2), events)
                release.set()
                results = await asyncio.gather(a, b)
                self.assertTrue(all(r.applied for r in results))
        asyncio.run(scenario())
        self.assertEqual(events, [('commit', 1), ('feed', 'working'), ('close', 1), ('connect', 2), ('commit', 2), ('feed', 'idle'), ('close', 2)])
        self.assertEqual(self.mirror.view()[(' Host ', 'CaseSensitive')].rows[0].state, 'idle')

    def test_hostile_view_and_projection_do_not_change_any_selected_outputs(self):
        from service.api_core import status_refresh, status_broadcast, status_inputs
        self.apply(body())
        self.agent['status'] = 'online'
        class DB:
            async def execute(inner, *a): return inner
            async def fetchone(inner): return self.agent
        async def scenario():
            for target in ('view', 'project_inputs'):
                owner = self.mirror if target == 'view' else self.shadow
                with patch.object(owner, target, side_effect=RuntimeError('private hostile')):
                    cache = {'status': 'available', 'reason': 'unchanged fallback', 'status_inputs': inputs(), 'refresh_after': 'fixed'}
                    with patch.object(status_refresh, '_compute_live_status_cache', AsyncMock(return_value=cache)):
                        result = await status_refresh._refresh_agent_live_state(DB(), 'alpha', settings={'x': True})
                        self.assertEqual((result['status'], result['reason'], result['refresh_after']), ('working', '', 'fixed'))
                    with patch.object(status_broadcast, '_compute_live_status_cache', AsyncMock(return_value=dict(cache))), \
                         patch.object(status_broadcast, '_load_settings', AsyncMock(return_value={'x': True})):
                        await status_broadcast._broadcast_agent_status(self.ws, DB(), 'alpha')
                    self.assertEqual(self.ws.broadcasts[-1][0][1], {'agentId': 'alpha', 'status': 'working', 'statusNote': ''})
                    with patch.object(status_inputs, '_gather_status_inputs', AsyncMock(return_value=inputs())) as get:
                        self.assertEqual(await status_inputs.engine_status(DB(), self.agent), 'working')
                        self.assertEqual(get.call_count, 1)
                    error = ValueError('original derive')
                    with patch.object(status_inputs, '_gather_status_inputs', AsyncMock(return_value=inputs())), \
                         patch.object(status_inputs, 'derive', side_effect=error):
                        with self.assertRaises(ValueError) as raised:
                            await status_inputs.engine_status(DB(), self.agent)
                        self.assertIs(raised.exception, error)
        asyncio.run(scenario())
        self.assertEqual(self.counts()['observed'], 8)
        self.assertEqual(self.counts()['unavailable'], 8)
        self.assertEqual(self.recorder.report()['byPurpose']['status word']['unavailableByReason']['unknown-loss'], 8)

    def test_pure_two_age_rows(self):
        rows = [('2026-10-07T09:02:59Z', 'fresh'), ('2026-10-07T09:03:00Z', 'stale')]
        for at, expected in rows:
            with self.subTest(at=at): self.assertEqual(self.shadow.age_class(AT, at), expected)

    def test_actual_accept_feed_is_immutable_and_fresh_comparison_is_not_vacuous(self):
        self.observe()
        self.assertEqual(self.counts()['unavailable'], 1)
        self.assertTrue(self.apply(body()).applied)
        bound = self.shadow.bind()
        self.apply(body(publication=2, agents=[dict(row(), state='idle')]))
        self.shadow.observe(bound, self.agent, inputs(), 'working', 'refresh')
        self.observe('working')
        self.assertEqual(self.counts()['partialAgreed'], 1)
        self.assertEqual(self.counts()['partialDisagreed'], 1)
        self.assertEqual(self.mirror.view()[(' Host ', 'CaseSensitive')].rows[0].state, 'idle')
        with self.assertRaises((AttributeError, TypeError)):
            bound.view[(' Host ', 'CaseSensitive')] = None

    def test_freshness_boundary_missing_invalid_future_and_unknown(self):
        self.apply(body())
        for at, expected in [('2026-10-07T09:02:59Z', 'partialAgreed'),
                             ('2026-10-07T09:03:00Z', 'unavailable'),
                             ('2026-10-07T08:59:59Z', 'unavailable'), ('bad', 'unavailable')]:
            before = self.counts()[expected]; self.observe(at=at)
            self.assertEqual(self.counts()[expected], before + 1)
        self.apply(body(publication=2, agents=[])); self.observe()
        self.apply(body(publication=3, agents=[dict(row(), state='unknown')])); self.observe()
        self.apply(body(publication=4), at='bad'); self.observe()
        self.assertEqual(self.counts()['partialCompared'], 1)
        self.assertEqual(self.counts()['unavailable'], 6)

    def test_generation_completeness_unavailable_retention_removal_and_recovery(self):
        self.apply(body('changes')); self.observe()
        self.assertEqual(self.counts()['partialCompared'], 0)
        self.apply(body(publication=2)); self.observe()
        self.apply(body('unavailable', generation=20)); self.observe()
        retained = self.mirror.view()[(' Host ', 'CaseSensitive')]
        self.assertEqual(retained.data_applied_at, AT)
        self.assertEqual(retained.rows[0].lifetime, 'real-old')
        self.apply(body('changes', generation=20, publication=2)); self.observe()
        self.assertEqual(self.counts()['partialCompared'], 1)
        self.apply(body(generation=20, publication=3)); self.observe()
        self.apply(body('changes', generation=20, publication=4, agents=[row(lifetime='new')],
                        removed=[{'agentId': 'alpha', 'lifetime': 'real-old'}]))
        self.apply(body('changes', generation=20, publication=5, agents=[],
                        removed=[{'agentId': 'alpha', 'lifetime': 'real-old'}])); self.observe()
        self.assertEqual(self.counts()['partialCompared'], 3)
        self.assertEqual(self.mirror.view()[(' Host ', 'CaseSensitive')].rows[0].lifetime, 'new')
        self.apply(body('changes', generation=20, publication=6, agents=[],
                        removed=[{'agentId': 'alpha', 'lifetime': 'new'}])); self.observe()
        self.assertEqual(self.counts()['unavailable'], 4)

    def test_refusal_rollback_and_postcommit_failure_do_not_lie_about_acceptance(self):
        from service.db import get_db
        self.apply(body()); before = self.mirror.view()
        self.assertFalse(self.apply(body()).applied)
        self.assertIs(self.mirror.view(), before)
        events = []
        class Connection:
            def __init__(self, db, fail=False): self.db, self.fail = db, fail
            async def execute(self, sql, args=()):
                if self.fail and sql.startswith('INSERT INTO agent_state_shadow_rows'):
                    raise sqlite3.OperationalError('fixture rollback')
                return await self.db.execute(sql, args)
            async def commit(self):
                await self.db.commit(); events.append('commit')
            async def rollback(self): events.append('rollback'); await self.db.rollback()
            async def close(self): events.append('close'); await self.db.close()
        async def failing(): return Connection(await get_db(), True)
        with self.assertRaises(sqlite3.OperationalError): self.apply(body(publication=2), factory=failing)
        self.assertIs(self.mirror.view(), before)
        async def factory(): return Connection(await get_db())
        def broken(projection): events.append('feed'); raise RuntimeError('private error text')
        events.clear()
        with patch.object(self.mirror, 'feed', side_effect=broken):
            self.assertTrue(self.apply(body(publication=2), factory=factory).applied)
        self.assertEqual(events, ['commit', 'feed', 'close'])
        self.observe(); self.assertEqual(self.counts()['unavailable'], 1)
        self.assertEqual(self.recorder.report()['byPurpose']['status word']['unavailableByReason']['unknown-loss'], 1)
        with closing(sqlite3.connect(self._db_path)) as db:
            self.assertEqual(db.execute('SELECT publication FROM agent_state_shadow_publishers').fetchone()[0], 2)
        self.apply(body(publication=3)); self.observe()
        self.assertEqual(self.counts()['partialAgreed'], 1)

    def test_actual_three_callers_capture_once_before_await_and_observe_selected_word(self):
        from service.api_core import status_refresh, status_broadcast, status_inputs
        self.apply(body())
        async def gather(*args, **kwargs):
            self.apply_in_loop = True
            # Advance mirror and clock during acquisition. The caller must use entry binding.
            self.mirror.invalidate((' Host ', 'CaseSensitive'))
            return inputs()
        async def cache(*args, **kwargs):
            self.mirror.invalidate((' Host ', 'CaseSensitive'))
            return {'status': 'available', 'reason': 'old note', 'status_inputs': inputs(),
                    'refresh_after': 'fixed', 'updated_at': 'fixed'}
        async def scenario():
            class DB:
                async def execute(self, *a): return self
                async def fetchone(self): return self_agent
            self_agent = self.agent
            with patch.object(status_refresh, '_compute_live_status_cache', side_effect=cache), \
                 patch.object(status_refresh, 'derive', wraps=status_refresh.derive) as derive:
                result = await status_refresh._refresh_agent_live_state(DB(), 'alpha', settings={'x': True})
                self.assertEqual(result['status'], 'working'); self.assertEqual(result['reason'], '')
                self.assertEqual(result['refresh_after'], 'fixed'); self.assertEqual(derive.call_count, 1)
            self.apply_projection()
            with patch.object(status_broadcast, '_compute_live_status_cache', side_effect=cache), \
                 patch.object(status_broadcast, '_load_settings', AsyncMock(return_value={'x': True})):
                await status_broadcast._broadcast_agent_status(self.ws, DB(), 'alpha')
            self.assertEqual(self.ws.broadcasts[-1][0][1], {'agentId': 'alpha', 'status': 'working', 'statusNote': ''})
            self.apply_projection()
            with patch.object(status_inputs, '_gather_status_inputs', side_effect=gather) as get, \
                 patch.object(status_inputs, 'derive', wraps=status_inputs.derive) as derive:
                self.assertEqual(await status_inputs.engine_status(DB(), self.agent), 'working')
                self.assertEqual(get.call_count, 1); self.assertEqual(derive.call_count, 1)
        with patch.object(self.shadow, 'clock', return_value=AT) as clock:
            asyncio.run(scenario()); self.assertEqual(clock.call_count, 3)
        for source in ('refresh', 'cache-broadcast', 'engine-status'):
            self.assertEqual(self.counts(source)['partialAgreed'], 1)

    def apply_projection(self):
        from service.api_core.agent_state_shadow import _decode, _validate
        b = _decode(wire(body())); _validate(b)
        self.mirror.feed(self.shadow.prepare(b, AT))

    def test_fallback_manual_binding_observe_failures_and_original_exception(self):
        from service.api_core import status_refresh, status_inputs
        self.apply(body())
        async def scenario():
            cache = {'status': 'online', 'reason': 'original', 'status_inputs': inputs()}
            with patch.object(status_refresh, '_compute_live_status_cache', AsyncMock(return_value=cache)), \
                 patch.object(status_refresh, 'derive', side_effect=ValueError('derive original')):
                result = await status_refresh._refresh_agent_live_state(None, 'alpha', agent_row=self.agent, settings={'x': 1})
                self.assertIs(result, cache); self.assertEqual(result['reason'], 'original')
            error = ValueError('same original')
            with patch.object(status_inputs, '_gather_status_inputs', AsyncMock(return_value=inputs())), \
                 patch.object(status_inputs, 'derive', side_effect=error):
                with self.assertRaises(ValueError) as raised:
                    await status_inputs.engine_status(None, self.agent)
                self.assertIs(raised.exception, error)
            with patch.object(self.shadow, 'clock', side_effect=RuntimeError('clock private')), \
                 patch.object(status_inputs, '_gather_status_inputs', AsyncMock(return_value=inputs())) as get:
                self.assertEqual(await status_inputs.engine_status(None, self.agent), 'working')
                self.assertEqual(get.call_count, 1)
            with patch.object(self.shadow.recorder, 'record', side_effect=RuntimeError('observe private')), \
                 patch.object(status_inputs, '_gather_status_inputs', AsyncMock(return_value=inputs())):
                self.assertEqual(await status_inputs.engine_status(None, self.agent), 'working')
            with patch.object(status_refresh, '_compute_live_status_cache', AsyncMock(return_value={'status': 'stopped'})), \
                 patch.object(status_refresh, 'derive') as derive:
                self.assertEqual((await status_refresh._refresh_agent_live_state(None, 'alpha', agent_row=self.agent,
                                                                              settings={'x': 1}))['status'], 'stopped')
                derive.assert_not_called()
        asyncio.run(scenario())
        self.assertEqual(self.counts()['partialDisagreed'], 1)
        self.assertEqual(self.counts()['nonEvaluation'], 1)
        self.assertGreaterEqual(self.counts()['unavailable'], 1)

    def test_read_only_report_auth_unknown_purposes_and_privacy(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from service.main import APIKeyMiddleware
        from service.routers.agent_state import router
        self.apply(body()); self.observe()
        app = FastAPI(); app.include_router(router, prefix='/api/v1')
        app.add_middleware(APIKeyMiddleware, api_key='fixture-key')
        before = self.recorder.report()
        with TestClient(app) as client:
            for headers in ({}, {'x-aify-agent-state-key': 'fixture-key'}):
                self.assertEqual(client.get('/api/v1/agent-state/shadow-report', headers=headers).status_code, 401)
            response = client.get('/api/v1/agent-state/shadow-report', headers={'X-API-Key': 'fixture-key'})
            self.assertEqual(response.status_code, 200); self.assertEqual(response.json(), before)
        self.assertEqual(before['scope'], 'partial-freshness-base-word')
        self.assertFalse(before['coverageComplete']); self.assertEqual(before['qualification'], 'UNAVAILABLE')
        self.assertEqual(len(before['byPurpose']), 10)
        for name, value in before['byPurpose'].items():
            if name != 'status word': self.assertIsNone(value['counts']); self.assertFalse(value['instrumented'])
        text = json.dumps(before)
        for secret in ('alpha', 'CaseSensitive', 'real-old', ' Host ', 'private', 'original'):
            self.assertNotIn(secret, text)
        counts = self.counts()
        self.assertEqual(counts['observed'], counts['nonEvaluation'] + counts['evaluated'])
        self.assertEqual(counts['evaluated'], counts['partialCompared'] + counts['unavailable'])

    def test_association_is_detached_before_acquisition_and_ambiguity_is_unavailable(self):
        from service.api_core import status_inputs
        self.apply(body())
        async def gather(*args, **kwargs):
            self.agent['machine_id'] = 'changed after await'
            return inputs()
        with patch.object(status_inputs, '_gather_status_inputs', side_effect=gather):
            self.assertEqual(asyncio.run(status_inputs.engine_status(None, self.agent)), 'working')
        self.assertEqual(self.counts()['partialAgreed'], 1)
        self.agent['machine_id'] = ' Host '
        self.apply(body(instance='other'))
        self.observe()
        self.assertEqual(self.recorder.report()['byPurpose']['status word']['unavailableByReason']['ambiguous-association'], 1)

    def test_engine_push_manual_and_missing_rows_are_non_evaluations(self):
        from service.api_core import status_broadcast
        self.agent.update(status='stopped', status_note='operator note')
        class DB:
            async def execute(inner, *args): return inner
            async def fetchone(inner): return self.agent
        class Missing(DB):
            async def fetchone(inner): return None
        async def scenario():
            with patch.object(status_broadcast, 'engine_status', AsyncMock()) as engine, \
                 patch.object(status_broadcast, '_load_settings', AsyncMock(return_value={'x': True})), \
                 patch.object(self.shadow, 'clock') as clock:
                await status_broadcast._broadcast_engine_status(self.ws, DB(), 'alpha')
                self.assertEqual(self.ws.broadcasts[-1][0][1],
                                 {'agentId': 'alpha', 'status': 'stopped', 'statusNote': 'operator note'})
                await status_broadcast._broadcast_engine_status(self.ws, Missing(), 'missing')
                self.assertEqual(len(self.ws.broadcasts), 1)
                engine.assert_not_called()
                clock.assert_not_called()
        asyncio.run(scenario())
        self.assertEqual(self.counts('engine-status')['nonEvaluation'], 2)
        self.assertEqual(self.counts('engine-status')['evaluated'], 0)
        self.assertEqual(self.counts('engine-status')['partialCompared'], 0)

    def test_missing_row_is_non_evaluation_and_engine_broadcast_does_not_duplicate(self):
        from service.api_core import status_refresh, status_broadcast, status_inputs
        self.apply(body())
        self.agent['status'] = 'online'
        class DB:
            async def execute(inner, *args): return inner
            async def fetchone(inner): return self.agent
        async def scenario():
            with patch.object(status_inputs, '_gather_status_inputs', AsyncMock(return_value=inputs())), \
                 patch.object(status_broadcast, '_load_settings', AsyncMock(return_value={'x': True})):
                await status_broadcast._broadcast_engine_status(self.ws, DB(), 'alpha')
            class Missing(DB):
                async def fetchone(inner): return None
            self.assertIsNone(await status_refresh._refresh_agent_live_state(Missing(), 'missing'))
        asyncio.run(scenario())
        self.assertEqual(self.counts()['partialAgreed'], 1)
        self.assertEqual(self.counts()['nonEvaluation'], 1)
        self.assertEqual(self.counts('cache-broadcast')['observed'], 0)
