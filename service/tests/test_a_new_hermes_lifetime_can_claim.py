"""Task #12: a hermes worker aify-env has just started reads `online` and takes its first message.

The real env host and publisher generate each row; the real ingest and channel claim
judge it. No runtime or console input runs. Missing publication and known busy are
withholding controls. The fresh row is the env host's own word for a child it started
before any turn (idle, at-prompt), not a synthetic idle.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import tempfile

from service.tests._base import FastApiTestCase
from service.tests.defined_agents import define


class ANewHermesLifetimeCanClaim(FastApiTestCase):
    def setUp(self):
        super().setUp()
        source = Path(os.environ.get('AIFY_ENV_REPO', Path.home() / 'projects/aify-env'))
        self.assertTrue((source / 'lib/agent-state-host.mjs').is_file(), 'need the actual env source')
        node = shutil.which('node')
        self.assertIsNotNone(node, 'need node to run the actual env producer')
        with tempfile.TemporaryDirectory() as home:
            generated = subprocess.run([node, str(Path(__file__).parent / 'data/hermes_initial_turn.mjs'),
                                        str(source), home], capture_output=True, text=True, timeout=30)
            self.assertEqual(generated.returncode, 0, generated.stderr)
            self.bodies = json.loads(generated.stdout)
        response = self.client.post('/api/v1/agents', json={
            'agentId': 'initial-hermes', 'role': 'coder', 'runtime': 'hermes',
            'sessionMode': 'managed', 'machineId': 'test-host',
            'capabilities': ['resume', 'interrupt'], 'runtimeConfig': {'channelEnabled': True}})
        self.assertEqual(response.status_code, 200, response.text)
        environment = self.client.post('/api/v1/environments/heartbeat', json={
            'id': 'test-host:default', 'machineId': 'test-host', 'bridgeId': 'fixture-host',
            'os': 'linux', 'kind': 'linux', 'cwdRoots': ['/workspace'],
            'runtimes': [{'runtime': 'hermes', 'modes': ['managed-warm'], 'capabilities': {}}],
            'terminal': True, 'pty': True, 'metadata': {'bridgeKind': 'aify-env'}})
        self.assertEqual(environment.status_code, 200, environment.text)
        define(self, 'initial-hermes', runtime='hermes', environment_id='test-host:default',
               machine_id='test-host', bridge_id='fixture-host')
        self.assertIsNone(self.claim()['run'], 'the live sidecar has no work before the send')
        sent = self.client.post('/api/v1/messages/send', json={
            'from_agent': 'dashboard', 'to': 'initial-hermes', 'type': 'request',
            'subject': 'first work', 'body': 'work without console input',
            'trigger': True, 'queueIfBusy': True})
        self.assertEqual(sent.status_code, 200, sent.text)
        runs = sent.json().get('dispatchRuns') or []
        self.assertEqual(len(runs), 1, sent.text)
        self.run_id = runs[0]['runId']

    def claim(self):
        response = self.client.post('/api/v1/dispatch/claim', json={
            'agentId': 'initial-hermes', 'machineId': 'test-host', 'bridgeId': 'initial-loop',
            'bridgeKind': 'channel-sidecar', 'executionModes': ['channel']})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def publish(self, name):
        response = self.client.post('/api/v1/agent-state', json=self.bodies[name])
        self.assertEqual(response.status_code, 204, response.text)

    def test_a_freshly_started_worker_reads_online_and_takes_the_first_claim(self):
        self.assertIsNone(self.claim()['run'], 'no publisher really withholds work')
        row = self.bodies['fresh']['agents'][0]
        self.assertEqual((row['state'], row['stateCause'], row['busy'], row['turn']),
                         ('idle', 'at-prompt', False, None))
        self.publish('fresh')
        self.assertEqual(self.client.get('/api/v1/agents/initial-hermes').json()['agent']['status'], 'online')
        self.assertEqual((self.claim()['run'] or {}).get('id'), self.run_id)

    def test_known_busy_holds_then_an_admitted_end_releases_the_same_run(self):
        self.publish('busy')
        self.assertIsNone(self.claim()['run'], 'positive busy observation must really hold explicit queue')
        with closing(sqlite3.connect(self._db_path)) as db:
            self.assertEqual(db.execute("SELECT status FROM dispatch_runs WHERE id=?", (self.run_id,)).fetchone()[0], 'queued')
        self.publish('closed')
        self.assertEqual((self.claim()['run'] or {}).get('id'), self.run_id)
