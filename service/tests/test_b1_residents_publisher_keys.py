"""B1 receiver controls: the residents' publications, made by REAL residents roles of private homes, through the
receiver and the partial-shadow decision exactly as they are. The receiver is not changed by B1; these prove it needs no
change: a successor on the same home-qualified key supersedes a crashed holder, a late body of the dead holder is
refused stale, and two homes on one machine are two publishers, never a conflict or a deletion.

The publications come from the aify-env checkout's own code (service/tests/data/b1_residents_wire.mjs): AIFY_ENV_REPO,
else beside this checkout, else ~/projects/aify-env.
"""
import asyncio
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from service.tests._base import FastApiTestCase

AT = '2026-10-14T09:00:00Z'
MACHINE = 'win32:b1-fixture'


def _env_checkout():
    repo = Path(__file__).resolve().parents[2]
    named = os.environ.get('AIFY_ENV_REPO')
    candidates = [Path(named)] if named else [repo.parent / 'aify-env', Path.home() / 'projects/aify-env']
    return next((p for p in candidates if (p / 'lib/daemon-residents.mjs').is_file()), None)


_PUBLICATIONS = None


def _publications():
    """The fixture's bodies, made once: it takes real locks and spawns the holder it crashes."""
    global _PUBLICATIONS
    if _PUBLICATIONS is None:
        source = _env_checkout()
        if source is None:
            raise AssertionError('AIFY_ENV_REPO must name an aify-env checkout with the B1 residents role')
        node = shutil.which('node')
        if node is None:
            raise AssertionError('Node must be available for the real producer')
        fixture = Path(__file__).with_name('data') / 'b1_residents_wire.mjs'
        with tempfile.TemporaryDirectory(prefix='b1-wire-') as tmp:
            result = subprocess.run([node, str(fixture), str(source), tmp], capture_output=True, check=True, timeout=60)
        _PUBLICATIONS = json.loads(result.stdout)
    return _PUBLICATIONS


class B1ResidentsPublisherKeysTest(FastApiTestCase):
    def setUp(self):
        super().setUp()
        from service.api_core import partial_status_shadow as shadow
        self.shadow = shadow
        for p in (patch.object(shadow, 'mirror', shadow.Mirror()),
                  patch.object(shadow, 'clock', return_value=AT)):
            p.start()
            self.addCleanup(p.stop)
        self.wire = _publications()

    def body(self, name, **override):
        return dict(json.loads(self.wire[name]), **override)

    def apply(self, name, **override):
        from service.api_core.agent_state_shadow import AgentStateShadowStore
        raw = self.wire[name].encode('utf-8') if not override else json.dumps(self.body(name, **override)).encode('utf-8')
        return asyncio.run(AgentStateShadowStore().apply(raw, AT))

    def rows(self):
        with closing(sqlite3.connect(self._db_path)) as db:
            return {(i, a): json.loads(r)['state'] for i, a, r in db.execute(
                'SELECT instance, agent_id, record_json FROM agent_state_shadow_rows WHERE machine_id=?', (MACHINE,))}

    def decide(self, agent):
        """The word the operational view reads for the agent from the mirror: what the service serves."""
        from service.api_core import operational_view as ov
        return ov.comms_view(ov.holders_of(self.shadow.bind(), MACHINE, agent), ov.Messaging()).status

    def test_the_fixture_publishes_from_real_roles_on_home_qualified_keys(self):
        first, successor = self.body('first'), self.body('successor')
        self.assertRegex(first['instance'], r'^residents-[0-9a-f]{12}$')
        self.assertEqual(first['instance'], successor['instance'], 'one home, one key across holders')
        self.assertEqual(successor['generation'], first['generation'] + 1, 'the successor took the next generation under the lock')
        self.assertNotEqual(first['incarnationId'], successor['incarnationId'])
        keys = {self.body(n)['instance'] for n in ('successor', 'equal', 'unequal')}
        self.assertEqual(len(keys), 3, 'three homes, three keys')
        self.assertEqual(self.body('equal')['generation'], successor['generation'])
        self.assertEqual({self.body(n)['machineId'] for n in self.wire}, {MACHINE}, 'one machine for every publisher')

    def test_a_crash_hands_the_key_to_b_and_the_late_a_body_is_stale(self):
        key = self.body('first')['instance']
        self.assertTrue(self.apply('first').applied)
        self.assertEqual(self.decide('res-r'), 'working', "A's snapshot is read")
        self.assertTrue(self.apply('successor').applied)
        self.assertEqual(self.rows(), {(key, 'res-r'): 'idle'}, "B's snapshot is the key's only rows")
        late = self.apply('delayed')
        self.assertEqual((late.applied, late.reason), (False, 'stale'))
        self.assertEqual(self.rows(), {(key, 'res-r'): 'idle'}, "a late A body changed B's rows")
        self.assertEqual(self.decide('res-r'), 'online', "the decision reads B's state, not A's")
        again = self.apply('first')
        self.assertEqual((again.applied, again.reason), (False, 'stale'), "A's own snapshot, replayed, is stale too")

    def test_a_non_holders_managed_key_beside_the_residents_key_is_no_ambiguity(self):
        self.assertTrue(self.apply('successor').applied)
        self.assertTrue(self.apply('managed').applied)
        self.assertEqual(self.rows(), {(self.body('successor')['instance'], 'res-r'): 'idle', ('default', 'worker-m'): 'working'})
        self.assertEqual(self.decide('res-r'), 'online')
        self.assertEqual(self.decide('worker-m'), 'working')

    def test_two_homes_on_one_machine_keep_both_rows_at_equal_and_unequal_generations(self):
        for name in ('successor', 'equal', 'unequal'):
            result = self.apply(name)
            self.assertTrue(result.applied, f'{name}: {result.reason}')
        self.assertEqual(self.rows(), {(self.body('successor')['instance'], 'res-r'): 'idle',
                                       (self.body('equal')['instance'], 'res-r2'): 'working',
                                       (self.body('unequal')['instance'], 'res-r3'): 'working'})
        self.assertEqual([self.decide(a) for a in ('res-r', 'res-r2', 'res-r3')],
                         ['online', 'working', 'working'], 'each resident read from its own home')
        replay = self.apply('equal')
        self.assertEqual((replay.applied, replay.reason), (False, 'duplicate'), 'only its own key orders a body')

    def test_control_one_unqualified_key_for_two_homes_would_conflict_and_delete(self):
        # NEGATIVE CONTROL for the revision-3 key: the same bodies on one shared key are what the home suffix prevents.
        shared = self.body('successor')['instance']
        self.assertTrue(self.apply('successor').applied)
        conflict = self.apply('equal', instance=shared)
        self.assertEqual((conflict.applied, conflict.reason), (False, 'conflict'))
        self.assertTrue(self.apply('unequal', instance=shared).applied)
        self.assertNotIn((shared, 'res-r'), self.rows(), 'a higher generation from another home deleted this home\'s row')
