"""Direct sends can wake a dead managed worker through the capability-gated lifecycle queue."""

import json
from pathlib import Path
import sqlite3

from service.tests._base import FastApiTestCase
from service.tests.defined_agents import define


class DispatchClaudeColdstartTests(FastApiTestCase):
    def _heartbeat_environment(self) -> None:
        response = self.client.post(
            "/api/v1/environments/heartbeat",
            json={
                "id": "linux:test-host:default",
                "label": "Linux on test-host",
                "machineId": "linux:test-host",
                "os": "linux",
                "kind": "linux",
                "bridgeId": "bridge-current",
                "cwdRoots": ["/workspace"],
                "terminal": True, "pty": True, "terminalRuntimes": ["claude-code"],
                "runtimes": [
                    {
                        "runtime": "claude-code",
                        "modes": ["managed-warm"],
                        "capabilities": {"nativeResume": True, "bridgeResume": True, "interrupt": True},
                    }
                ],
                "metadata": {},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)

    def _register_dead_managed_claude(self, agent_id: str) -> None:
        response = self.client.post(
            "/api/v1/agents",
            json={
                "agentId": agent_id,
                "role": "coder",
                "runtime": "claude-code",
                "sessionMode": "managed",
                "machineId": "linux:test-host",
                "bridgeId": "bridge-current",
                "capabilities": ["managed-run", "resume", "interrupt"],
                "runtimeConfig": {"channelEnabled": True},
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        conn = sqlite3.connect(str(self._db_path))
        try:
            # The post-env-restart state: a session exists but is STOPPED, no terminal,
            # no live managed-wrapper-child bridge.
            conn.execute(
                """
                INSERT INTO agent_sessions (
                    id, agent_id, environment_id, runtime, workspace, mode, owner_mode,
                    terminal_id, terminal_status, status, started_at, last_seen
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    f"session-{agent_id}", agent_id, "linux:test-host:default", "claude-code",
                    "/workspace", "managed-warm", "managed", "", "", "stopped",
                    "2026-06-12T00:00:00Z", "2026-06-12T00:00:00Z",
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _spawn_requests_for(self, agent_id: str) -> list[sqlite3.Row]:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        try:
            return list(conn.execute(
                "SELECT id, status, runtime, environment_id FROM spawn_requests WHERE agent_id = ?",
                (agent_id,),
            ).fetchall())
        finally:
            conn.close()

    def test_two_direct_sends_to_a_dead_worker_queue_one_automatic_start(self):
        self._heartbeat_environment()
        self._register_dead_managed_claude("claude-cold")
        # Since D8 only an agent its host defines is cold-started by a message.
        define(self, "claude-cold", environment_id="linux:test-host:default", machine_id="linux:test-host",
               bridge_id="bridge-current", runtime="claude-code", workspace="/workspace")
        for _ in range(2):
            response = self.client.post(
                "/api/v1/messages/send",
                json={
                    "from_agent": "dashboard",
                    "trigger": True,
                    "to": "claude-cold",
                    "type": "request",
                    "subject": "coldstart-test",
                    "body": "wake up",
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
        request_path = '/api/v1/agents/claude-cold/lifecycle-requests'
        [pending] = self.client.get(request_path).json()['requests']
        self.assertEqual({key: pending[key] for key in ('action', 'startIntent', 'expectedLifetime', 'freshContext', 'machineId', 'status')},
                         {'action': 'start', 'startIntent': 'automatic', 'expectedLifetime': None,
                          'freshContext': False, 'machineId': 'linux:test-host', 'status': 'pending'})
        self.assertEqual(self._spawn_requests_for('claude-cold'), [])

        # Captured from LifecycleSync -> CommsApi at the pinned Env commit, with fake fetch.
        fixture = Path(__file__).with_name('fixtures') / 'env-lifecycle-claim-32c7dc42.json'
        wire = json.loads(fixture.read_text(encoding='utf-8'))
        capable = wire['body']
        host = {key: value for key, value in capable.items() if key != 'capabilities'}

        def claim(body):
            response = self.client.request(wire['method'], wire['path'], json=body)
            self.assertEqual(response.status_code, 200, response.text)
            return response.json()['requests']

        define(self, 'explicit', environment_id='linux:test-host:default', machine_id='linux:test-host',
               bridge_id='bridge-current')
        ordinary_body = {'requestId': 'operator-start', 'action': 'start', 'requestedBy': 'dashboard',
                         'expectedLifetime': None}
        explicit_path = '/api/v1/agents/explicit/lifecycle-requests'
        for value in ('automatic', None):
            rejected = self.client.post(explicit_path, json={**ordinary_body, 'startIntent': value})
            self.assertEqual(rejected.status_code, 422, rejected.text)
            self.assertEqual(rejected.json()['detail'], 'startIntent is reserved for automatic dispatch')
            self.assertEqual(self.client.get(explicit_path).json()['requests'], [])
        ordinary = self.client.post(explicit_path, json=ordinary_body)
        self.assertEqual(ordinary.status_code, 200, ordinary.text)
        [ordinary_claim] = claim(host)
        self.assertEqual(ordinary_claim['id'], 'operator-start')
        self.assertNotIn('startIntent', ordinary_claim)
        self.assertEqual(self.client.get(request_path).json()['requests'], [pending])

        for value in ([], ['automatic-start-v10'], 'automatic-start-v1', ['automatic-start-v1', None]):
            with self.subTest(capabilities=value):
                self.assertEqual(claim({**host, 'capabilities': value}), [ordinary_claim])
                self.assertEqual(self.client.get(request_path).json()['requests'], [pending])
        automatic, = [row for row in claim(capable) if row['id'] != ordinary_claim['id']]
        self.assertTrue(automatic['claimedAt'])
        self.assertEqual(automatic, {**pending, 'status': 'claimed', 'claimedAt': automatic['claimedAt']})
        self.assertEqual(claim(host), [ordinary_claim])
        self.assertEqual(self.client.get(request_path).json()['requests'], [automatic])
        self.assertCountEqual(claim(capable), [ordinary_claim, automatic])

    def test_a_cold_send_explains_the_wait_until_its_host_advertises_automatic_start(self):
        self._heartbeat_environment()
        wire = json.loads((Path(__file__).with_name('fixtures') / 'env-lifecycle-claim-32c7dc42.json')
                          .read_text(encoding='utf-8'))
        host = dict(wire['body'])
        host.pop('capabilities')

        def claim(body):
            response = self.client.request(wire['method'], wire['path'], json=body)
            response.raise_for_status()
            return response.json()['requests']

        def send(agent_id):
            self._register_dead_managed_claude(agent_id)
            define(self, agent_id, environment_id='linux:test-host:default', machine_id='linux:test-host',
                   bridge_id='bridge-current')
            response = self.client.post('/api/v1/messages/send', json={
                'from_agent': 'dashboard', 'trigger': True, 'to': agent_id,
                'type': 'request', 'subject': 'cold start', 'body': 'wake up',
            })
            response.raise_for_status()
            self.assertEqual(self._spawn_requests_for(agent_id), [])
            return response.json()

        # A modern observation must be replaced, not accumulated, when the next proven claim omits it.
        self.assertEqual(claim(wire['body']), [])
        self.assertEqual(claim(host), [])
        old = send('waiting-agent')
        reason = ('the start waits for aify-env with automatic-start-v1 on linux:test-host; '
                  'restart aify-env on 32c7dc42 or later')
        self.assertIn(reason, old['warnings'])
        [run] = old['dispatchRuns']
        self.assertEqual(run['waitingReason'], reason)
        run_path = f"/api/v1/dispatch/runs/{run['runId']}"
        polled = self.client.get(run_path).json()['run']
        self.assertEqual((polled['status'], polled['waitingReason'], polled['error']), ('queued', reason, ''))
        [listed] = self.client.get('/api/v1/dispatch/runs?agentId=waiting-agent').json()['runs']
        self.assertEqual(listed['waitingReason'], reason)
        self.assertEqual(claim(host), [])

        # A different bridge may neither claim nor rewrite the observed capability.
        forged = self.client.request(wire['method'], wire['path'], json={**wire['body'], 'bridgeId': 'retired'})
        self.assertEqual(forged.status_code, 409, forged.text)
        self.assertEqual(self.client.get(run_path).json()['run']['waitingReason'], reason)
        [claimed] = claim(wire['body'])
        self.assertEqual(claimed['agentId'], 'waiting-agent')
        self.assertNotIn('waitingReason', self.client.get(run_path).json()['run'])
        modern = send('capable-agent')
        self.assertNotIn(reason, modern['warnings'])
        [ready] = modern['dispatchRuns']
        self.assertNotIn('waitingReason', ready)

    def test_an_automatic_launch_uses_only_a_compatible_native_handle(self):
        self._heartbeat_environment()
        wire = json.loads((Path(__file__).with_name('fixtures') / 'env-lifecycle-claim-32c7dc42.json')
                          .read_text(encoding='utf-8'))
        # The newest session belongs to the wrong harness. A same-runtime pin wins; without one,
        # only the same agent's latest compatible session can resume. No compatible session means fresh.
        for agent_id, runtime, pinned, prior, wanted in (
            ('pinned', 'claude-code', 'current-native', 'prior-native', 'current-native'),
            ('fallback', 'claude-code', '', 'prior-native', 'prior-native'),
            ('wrong-runtime-pin', 'codex', 'wrong-pin', 'prior-native', 'prior-native'),
            ('no-compatible-session', 'claude-code', '', '', ''),
        ):
            with self.subTest(agent=agent_id):
                self._register_dead_managed_claude(agent_id)
                define(self, agent_id, environment_id='linux:test-host:default', machine_id='linux:test-host',
                       bridge_id='bridge-current')
                with sqlite3.connect(self._db_path) as db:
                    db.execute('UPDATE agents SET session_handle = ?, runtime = ? WHERE id = ?',
                               (pinned, runtime, agent_id))
                    db.execute('UPDATE agent_sessions SET session_handle = ? WHERE id = ?',
                               (prior, f'session-{agent_id}'))
                    db.execute('''INSERT INTO agent_sessions
                        (id, agent_id, environment_id, runtime, session_handle, status, started_at, last_seen)
                        VALUES (?, ?, 'linux:test-host:default', 'codex', 'wrong-native', 'stopped',
                                '2026-06-13T00:00:00Z', '2026-06-13T00:00:00Z')''', (f'codex-{agent_id}', agent_id))
                db.close()
                response = self.client.post('/api/v1/messages/send', json={
                    'from_agent': 'dashboard', 'trigger': True, 'to': agent_id,
                    'type': 'request', 'subject': 'resume', 'body': 'wake up',
                })
                self.assertEqual(response.status_code, 200, response.text)
                reply = self.client.request(wire['method'], wire['path'], json=wire['body'])
                reply.raise_for_status()
                receipt, = [row for row in reply.json()['requests'] if row['agentId'] == agent_id]
                launch_path = wire['path'].removesuffix('/claim') + f"/{receipt['id']}/launch"
                launched = self.client.post(launch_path, json=wire['body'])
                self.assertEqual(launched.status_code, 200, launched.text)
                launch = launched.json()['launch']
                self.assertEqual(launch['sessionHandle'], wanted)
                self.assertEqual(launch['env']['AIFY_SESSION_HANDLE'], wanted)
                self.assertNotIn('wrong-native', launch['argv'])
                if wanted:
                    self.assertIn(wanted, launch['argv'])
                self.assertEqual(self._spawn_requests_for(agent_id), [])

    def test_wrapper_backing_after_a_mode_switch_queues_an_automatic_start(self):
        import asyncio
        import logging

        from service.api_core.session_mode_gates import _start_managed_backing_after_switch
        from service.clock import now
        from service.db import get_db

        self._heartbeat_environment()
        self.client.post('/api/v1/environments/heartbeat', json={
            'id': 'linux:codex-host:default', 'machineId': 'linux:codex-host', 'bridgeId': 'codex-host',
            'os': 'linux', 'kind': 'linux', 'cwdRoots': ['/workspace'],
            'runtimes': [{'runtime': 'codex', 'modes': ['managed-warm']}],
        }).raise_for_status()
        define(self, 'switched', environment_id='linux:codex-host:default', machine_id='linux:codex-host',
               bridge_id='codex-host', runtime='codex')
        path = '/api/v1/agents/switched/lifecycle-requests'
        self.assertEqual(self.client.get(path).json()['requests'], [], 'publishing a definition starts nothing')

        async def ensure_backing():
            db = await get_db()
            try:
                effects = {}
                await _start_managed_backing_after_switch(db, 'switched', 'managed', 'codex',
                    {'managed_via_wrapper': True}, 'operator', now(), logging.getLogger(__name__), effects)
                await db.commit()
                return effects
            finally:
                await db.close()

        self.assertEqual(asyncio.run(ensure_backing()), {'managedSpawnRequested': True})
        [receipt] = self.client.get(path).json()['requests']
        self.assertEqual((receipt['action'], receipt['startIntent'], receipt['machineId']),
                         ('start', 'automatic', 'linux:codex-host'))
        self.assertEqual(self._spawn_requests_for('switched'), [])
