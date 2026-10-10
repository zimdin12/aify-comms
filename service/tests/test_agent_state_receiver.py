"""G8a ingest only. No auth middleware, lifecycle or current-status evaluation."""
import asyncio
import copy
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
from unittest.mock import patch

from service.tests._base import FastApiTestCase


AT = '2026-10-07T09:00:00Z'
LATER = '2026-10-07T09:01:00Z'


def row(agent='alpha', lifetime='real-old'):
    return dict(agentId=agent, lifetime=lifetime, state='working', stateCause='turn-open', busy=True,
                process=dict(state='running', verified='yes', pid=4242),
                turn=dict(open=True, startedAtUs=1700000000000000, lastEventAtUs=1700000000000000,
                          awaitingInput=False, busyIf=dict(strict=True, verifiedRenewal=True)))


def body(kind='snapshot', generation=10, publication=1, **extra):
    value = dict(kind=kind, machineId=' Host ', instance='CaseSensitive', generation=generation,
                 incarnationId='boot', publication=publication, inputs={'operatorStop': 'not-tracked'})
    if kind == 'unavailable':
        value['reason'] = 'observation-incomplete'
    else:
        value.update(agents=[row()], removed=[])
        if kind == 'snapshot':
            value['complete'] = True
    value.update(extra)
    return value


def wire(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


class AgentStateReceiverTest(FastApiTestCase):
    def post(self, value, at=AT):
        # Patch at the route clock boundary, not in storage.
        with patch('service.clock.now', return_value=at):
            return self.client.post('/api/v1/agent-state', content=value if isinstance(value, bytes) else wire(value),
                                    headers={'content-type': 'application/json'})

    def sql(self, query, params=()):
        with closing(sqlite3.connect(self._db_path)) as db:
            db.row_factory = sqlite3.Row
            return [dict(record) for record in db.execute(query, params).fetchall()]

    def stored(self):
        return self.sql('SELECT * FROM agent_state_shadow_publishers'), self.sql('SELECT * FROM agent_state_shadow_rows ORDER BY machine_id,instance,agent_id')

    def apply(self, value, at=AT, factory=None):
        from service.api_core.agent_state_shadow import AgentStateShadowStore
        store = AgentStateShadowStore() if factory is None else AgentStateShadowStore(factory)
        return asyncio.run(store.apply(value if isinstance(value, bytes) else wire(value), applied_at=at))

    def test_composed_http_accepts_with_bodyless_204(self):
        response = self.client.post('/api/v1/agent-state', content=wire(body()), headers={'content-type': 'application/json'})
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(response.content, b'')

    def test_c1_fields_are_read_when_present(self):
        named = row()
        named.update(name='Alpha', role='coder', launch={'cwd': 'C:/work', 'definition': {'storeId': 's', 'incarnation': 1, 'revision': 3}})
        bare = row('beta')
        bare.update(name=None, role=None, launch={'cwd': None, 'definition': None})
        response = self.client.post('/api/v1/agent-state', content=wire(body(agents=[named, bare])),
                                    headers={'content-type': 'application/json'})
        self.assertEqual(response.status_code, 204, response.text)

    def test_protected_real_ingest_refuses_before_writing(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from service.main import APIKeyMiddleware
        from service.routers.agent_state import router
        app = FastAPI()
        app.include_router(router, prefix='/api/v1')
        app.add_middleware(APIKeyMiddleware, api_key='fixture-only-key')
        before = self.stored()
        with TestClient(app) as client:
            for headers in ({}, {'x-aify-agent-state-key': 'wrong'}):
                self.assertEqual(client.post('/api/v1/agent-state', content=wire(body()), headers=headers).status_code, 401)
                self.assertEqual(self.stored(), before)
            response = client.post('/api/v1/agent-state', content=wire(body()),
                                   headers={'x-aify-agent-state-key': 'fixture-only-key'})
            self.assertEqual(response.status_code, 204, response.text)
            self.assertEqual(response.content, b'')
            self.assertEqual(len(self.stored()[1]), 1)

    def test_order_table_and_intersections_do_not_renew(self):
        original = wire(body())
        self.assertTrue(self.apply(original).applied)
        before = self.stored()
        cases = [(original, 'duplicate'), (wire(body(publication=1, inputs={'x': 'different'})), 'conflict'),
                 (wire(body(publication=1)).replace(b':', b': '), 'conflict'),
                 (wire(body(generation=9, incarnationId='different', publication=99)), 'stale'),
                 (wire(body(incarnationId='different', publication=1)), 'conflict'),
                 (wire(body(incarnationId='different', publication=0+1)), 'conflict')]
        # Publication zero is invalid, so first advance to make lower-positive intersection reachable.
        for raw, reason in cases:
            result = self.apply(raw, LATER)
            self.assertFalse(result.applied)
            self.assertEqual(result.reason, reason)
            self.assertEqual(self.stored(), before)
        self.assertTrue(self.apply(body(publication=8), LATER).applied)
        before = self.stored()
        for incarnation, expected in [('boot', 'stale'), ('other', 'conflict')]:
            result = self.apply(body(publication=2, incarnationId=incarnation), 'future')
            self.assertEqual(result.reason, expected)
            self.assertEqual(self.stored(), before)
        from dataclasses import FrozenInstanceError
        with self.assertRaises(FrozenInstanceError):
            result.reason = 'changed'

    def test_unavailable_cursor_and_deferred_generation_clear(self):
        self.apply(body(agents=[row(), row('beta', None)]))
        self.apply(body(machineId='other', agents=[row('foreign')]))
        rows_before = self.stored()[1]
        response = self.post(body('unavailable', generation=20, publication=1), LATER)
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(self.stored()[1], rows_before)
        publisher = self.sql('SELECT * FROM agent_state_shadow_publishers WHERE machine_id=?', (' Host ',))[0]
        self.assertEqual(publisher['generation'], 20)
        self.assertEqual(publisher['data_generation'], 10)
        self.assertEqual(publisher['data_applied_at'], AT)
        self.assertEqual(self.apply(body(generation=10, publication=99)).reason, 'stale')
        self.apply(body('changes', generation=20, publication=7, agents=[row('new')]), LATER)
        self.assertEqual({r['agent_id'] for r in self.stored()[1]}, {'new', 'foreign'})
        publisher = self.sql('SELECT * FROM agent_state_shadow_publishers WHERE machine_id=?', (' Host ',))[0]
        self.assertEqual(publisher['data_generation'], 20)
        self.assertEqual(publisher['data_applied_at'], LATER)
        self.assertEqual(json.loads(publisher['data_inputs']), {'operatorStop': 'not-tracked'})

    def test_snapshot_scope_null_rows_literal_lifetimes_and_row_provenance(self):
        self.apply(body(agents=[row(), row('nullable', None)]))
        self.apply(body(instance='other', agents=[row()]))
        original = self.stored()[1]
        self.assertIsNone(next(r for r in original if r['agent_id'] == 'nullable')['lifetime'])
        self.apply(body('changes', publication=2, agents=[row(lifetime='Real New ')],
                        removed=[{'agentId': 'alpha', 'lifetime': 'real-old'}]), LATER)
        self.apply(body('changes', publication=3, agents=[], removed=[{'agentId': 'alpha', 'lifetime': 'real-old'}]), 'later')
        records = self.sql('SELECT * FROM agent_state_shadow_rows WHERE instance=?', ('CaseSensitive',))
        alpha = next(r for r in records if r['agent_id'] == 'alpha')
        self.assertEqual(alpha['lifetime'], 'Real New ')
        self.assertEqual(alpha['row_applied_at'], LATER)
        self.assertEqual(next(r for r in records if r['agent_id'] == 'nullable')['row_applied_at'], AT)
        self.apply(body(publication=4, agents=[], removed=[{'agentId': 'alpha', 'lifetime': 'Real New '}]))
        self.assertEqual(len(self.stored()[1]), 1)
        self.assertEqual(self.stored()[1][0]['instance'], 'other')

    def test_exact_wire_digest_literal_json_and_clock_value(self):
        value = body(agents=[row('Ünicode', None)])
        raw = wire(value)
        self.post(raw, 'future-provenance-verbatim')
        publishers, records = self.stored()
        self.assertEqual(publishers[0]['digest'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(publishers[0]['data_applied_at'], 'future-provenance-verbatim')
        self.assertEqual(records[0]['row_applied_at'], 'future-provenance-verbatim')
        self.assertEqual(json.loads(records[0]['record_json']), value['agents'][0])
        self.assertEqual(publishers[0]['machine_id'], ' Host ')

    def test_whole_validation_before_order_including_stale_tails(self):
        self.apply(body(publication=8))
        before = self.stored()
        invalid = []
        for kind in ('snapshot', 'changes', 'unavailable'):
            missing = body(kind, generation=9)
            del missing['inputs']
            invalid.extend([missing, body(kind, generation=9, inputs={'x': 1}), body(kind, unexpected=True)])
        for counter in (True, 1.0, 0, -1, 9007199254740992):
            invalid.extend([body(generation=counter), body(publication=counter)])
        for change in ({'state': 'invented'}, {'stateCause': 'invented'}, {'busy': 1}, {'lifetime': ''},
                       {'process': dict(state='running', verified='maybe', pid=1)},
                       {'process': dict(state='invented', verified='yes', pid=1)},
                       {'process': dict(state='running', verified='yes', pid=True)},
                       {'process': dict(state='running', verified='yes', pid=0)},
                       {'process': dict(state='none', verified='no', pid=None, extra=True)},
                       {'turn': dict(open=True, startedAtUs=-1, lastEventAtUs=1, awaitingInput=False, busyIf=dict(strict=True, verifiedRenewal=True))},
                       {'extra': True},
                       # C1's optional fields, each malformed.
                       {'name': ''}, {'role': 1}, {'launch': {'cwd': None}}, {'launch': {'cwd': '', 'definition': None}},
                       {'launch': {'cwd': None, 'definition': {'storeId': 's', 'incarnation': 0, 'revision': 1}}},
                       {'launch': {'cwd': None, 'definition': {'storeId': 's', 'incarnation': 1}}}):
            bad_row = row('bad'); bad_row.update(change)
            invalid.append(body(generation=9, agents=[row(), bad_row]))
        for field, value in [('open', 1), ('startedAtUs', 1.5), ('lastEventAtUs', True), ('awaitingInput', 'no'),
                             ('busyIf', {'strict': 1, 'verifiedRenewal': False}), ('busyIf', {'strict': False})]:
            bad_row = row('bad'); bad_row['turn'][field] = value
            invalid.append(body(generation=9, agents=[row(), bad_row]))
        invalid.extend([body(agents=[row(), row()]), body(complete=False), body('changes', complete=True),
                        body('unavailable', agents=[]), body('unavailable', reason=''), body(agents={}),
                        body(removed=[{'agentId': 'alpha', 'lifetime': None}]),
                        body(removed=[{'agentId': 'alpha', 'lifetime': ''}]),
                        body(removed=[{'agentId': 'alpha', 'lifetime': 'real', 'extra': True}])])
        for value in invalid:
            with self.subTest(value=value):
                response = self.post(value)
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(self.stored(), before)
        raw_cases = [b'\xff', b'[]', b'null', b'{', wire(body()).replace(b'"generation":10', b'"generation":' + b'9' * 5000),
                     wire(body()).replace(b'"generation":10', b'"generation":NaN'),
                     wire(body()).replace(b'"busy":true', b'"busy":true,"b\\u0075sy":false'),
                     wire(body()).replace(b'"strict":true', b'"strict":true,"strict":false'),
                     wire(body()).replace(b'"kind":"snapshot"', b'"kind":"snapshot","kind":"changes"')]
        for raw in raw_cases:
            self.assertEqual(self.post(raw).status_code, 422)
            self.assertEqual(self.stored(), before)
        from service.api_core.agent_state_shadow import InvalidPublication
        self.assertTrue(issubclass(InvalidPublication, ValueError))
        with self.assertRaises(InvalidPublication):
            self.apply(raw_cases[0])

    def test_transaction_rolls_back_rows_and_cursor_and_closes(self):
        from service.db import get_db
        self.apply(body())
        before = self.stored()
        calls = []
        class FailConnection:
            def __init__(self, connection): self.connection = connection
            async def execute(self, sql, params=()):
                calls.append(sql)
                if sql.startswith('INSERT INTO agent_state_shadow_rows'):
                    raise sqlite3.OperationalError('injected row failure')
                return await self.connection.execute(sql, params)
            async def commit(self): calls.append('commit'); await self.connection.commit()
            async def rollback(self): calls.append('rollback'); await self.connection.rollback()
            async def close(self): calls.append('close'); await self.connection.close()
        async def factory(): return FailConnection(await get_db())
        with self.assertRaisesRegex(sqlite3.OperationalError, 'injected'):
            self.apply(body(generation=20), factory=factory)
        self.assertEqual(self.stored(), before)
        self.assertEqual(calls[0], 'BEGIN IMMEDIATE')
        self.assertEqual(calls[-2:], ['rollback', 'close'])
        self.assertNotIn('commit', calls)
        self.assertTrue(self.apply(body(publication=2)).applied)

    def test_single_commit_and_no_current_authority_side_effects(self):
        from service.db import get_db
        from service.reconcilers.status_cache import _LIVE_STATE_CACHE
        tables = ('agents', 'agent_turn_state', 'agent_status_state', 'messages', 'environments')
        before = {t: self.sql('SELECT * FROM ' + t) for t in tables}
        _LIVE_STATE_CACHE['sentinel'] = {'untouched': True}
        cache_before = copy.deepcopy(_LIVE_STATE_CACHE)
        calls = []
        class TracedConnection:
            def __init__(self, connection): self.connection = connection
            async def execute(self, sql, params=()): calls.append(sql); return await self.connection.execute(sql, params)
            async def commit(self): calls.append('commit'); await self.connection.commit()
            async def rollback(self): calls.append('rollback'); await self.connection.rollback()
            async def close(self): calls.append('close'); await self.connection.close()
        async def factory(): return TracedConnection(await get_db())
        self.assertTrue(self.apply(body(), factory=factory).applied)
        self.assertEqual(calls[0], 'BEGIN IMMEDIATE')
        self.assertEqual(calls.count('commit'), 1)
        self.assertEqual(calls[-1], 'close')
        self.assertEqual(before, {t: self.sql('SELECT * FROM ' + t) for t in tables})
        self.assertEqual(cache_before, _LIVE_STATE_CACHE)
        self.assertEqual(self.ws.broadcasts, [])
        self.assertEqual(self.ws.notifications, [])

    def test_actual_offline_node_g6_publisher_to_http_receiver(self):
        # Only child identity/definition and launcher observations are synthetic.
        # All G6 derivation/read and G7 wire construction execute sibling production JS.
        # The gate receipt binds that checkout independently.
        repo = Path(__file__).resolve().parents[2]
        named = os.environ.get('AIFY_ENV_REPO')
        candidates = [Path(named)] if named else [repo.parent/'aify-env', Path.home()/'projects/aify-env']
        source = next((p for p in candidates if (p/'lib/agent-state-publisher.mjs').is_file()), None)
        self.assertIsNotNone(source, 'AIFY_ENV_REPO must name a checkout with the G7 publisher')
        node = shutil.which('node')
        self.assertIsNotNone(node, 'Node must be available for the real producer compatibility test')
        fixture = Path(__file__).with_name('data')/'g6_agent_state_wire.mjs'
        result = subprocess.run([node, str(fixture), str(source), self._tmpdir.name],
                                capture_output=True, check=True, timeout=20)
        publications = json.loads(result.stdout)
        self.assertEqual([json.loads(raw)['kind'] for raw in publications], ['snapshot', 'changes', 'changes', 'unavailable'])
        for index, raw in enumerate(publications):
            response = self.post(raw.encode('utf-8'), AT if index == 0 else LATER)
            self.assertEqual(response.status_code, 204, response.text)
            if index == 0:
                records = self.stored()[1]
                self.assertEqual({r['agent_id'] for r in records}, {'alpha', 'nullable'})
                self.assertIsNone(next(r for r in records if r['agent_id'] == 'nullable')['lifetime'])
            if index == 1:
                self.assertEqual(next(r for r in self.stored()[1] if r['agent_id'] == 'alpha')['lifetime'], '22222222-2222-4222-8222-222222222222')
            if index == 2:
                rows_before = self.stored()[1]
                self.assertEqual([r['agent_id'] for r in rows_before], ['nullable'])
            if index == 3:
                self.assertEqual(self.stored()[1], rows_before)
        response = self.post(publications[-1].encode())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'applied': False, 'reason': 'duplicate'})
