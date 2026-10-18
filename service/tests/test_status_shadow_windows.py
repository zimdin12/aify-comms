"""The persisted shadow windows (status-switch plan S2a): continuity is recorded, never assumed.

Each test names the interruption it witnesses. A window closes at a restart (at its last flush), when a publication
loss begins and when it ends, when a version changes, and when a flush is lost; its counts are deltas, so a closed
window never absorbs a later one.
"""
import asyncio
from unittest.mock import patch

from service.tests._base import FastApiTestCase


class ShadowWindowsTest(FastApiTestCase):
    def setUp(self):
        super().setUp()
        from service.api_core import partial_status_shadow as shadow
        from service.api_core import shadow_windows as windows
        self.shadow, self.windows = shadow, windows
        self.recorder = shadow.Recorder()
        self.v1 = windows.Versions(comms='aaaa1111')

    def owner(self, recorder=None):
        return self.windows.ShadowWindows(recorder or self.recorder)

    def wait(self, coroutine):
        return asyncio.run(coroutine)

    def all_windows(self):
        return list(reversed(self.wait(self.windows.recent_windows(200))))

    def counts(self, window):
        return window['counts'].get('status word', {})

    def test_counts_are_deltas_in_the_window_they_were_recorded_in(self):
        owner = self.owner()
        self.recorder.record('refresh', 'blocked', 'online')  # before the window opened: in no window
        self.wait(owner.start(self.v1, '2026-10-18T10:00:00Z'))
        self.recorder.record('refresh', 'online', 'online')
        self.wait(owner.tick(self.v1, '2026-10-18T10:00:30Z'))
        self.recorder.record('refresh', 'online', 'working', inputs=(('mode', 'resident'),))
        self.recorder.record('engine-status', non_evaluation=True)
        self.wait(owner.tick(self.v1, '2026-10-18T10:01:00Z'))
        [window] = self.all_windows()
        self.assertEqual(self.counts(window)['refresh'], {
            'observed': 2, 'evaluated': 2, 'partialCompared': 2, 'partialAgreed': 1, 'partialDisagreed': 1,
            'disagree:resident:online:working': 1})
        self.assertEqual(self.counts(window)['engine-status'], {'observed': 1, 'nonEvaluation': 1})
        self.assertEqual(window['flushedAt'], '2026-10-18T10:01:00Z')
        self.assertIsNone(window['closedAt'])
        self.assertEqual(window['versions'], {'comms': 'aaaa1111', 'env': None, 'wrapper': None},
                         'an unknown env or wrapper build is recorded as unknown, never guessed')

    def test_a_restart_closes_the_open_window_at_its_last_flush_and_keeps_its_history(self):
        first = self.owner()
        self.wait(first.start(self.v1, '2026-10-18T10:00:00Z'))
        self.recorder.record('refresh', 'online', 'online')
        self.wait(first.tick(self.v1, '2026-10-18T10:00:30Z'))
        self.recorder.record('refresh', 'online', 'online')  # recorded, never flushed: lost with the process
        second = self.owner(self.shadow.Recorder())
        self.wait(second.start(self.v1, '2026-10-18T10:05:00Z'))
        old, new = self.all_windows()
        self.assertEqual((old['closedAt'], old['closeReason']), ('2026-10-18T10:00:30Z', 'service-restart'))
        self.assertEqual(self.counts(old)['refresh']['observed'], 1)
        self.assertEqual((new['openedAt'], new['closedAt'], new['counts']), ('2026-10-18T10:05:00Z', None, {}))

    def test_a_publication_loss_closes_the_window_when_it_begins_and_when_it_ends(self):
        owner = self.owner()
        self.wait(owner.start(self.v1, '2026-10-18T10:00:00Z'))
        self.recorder.record('refresh', 'online', 'online')
        self.wait(owner.tick(self.v1, '2026-10-18T10:00:30Z'))
        self.recorder.record('refresh', reason='unknown-loss')
        self.wait(owner.tick(self.v1, '2026-10-18T10:01:00Z'))
        self.recorder.record('refresh', reason='unknown-loss')
        self.wait(owner.tick(self.v1, '2026-10-18T10:01:30Z'))  # loss continues: same window
        self.recorder.record('refresh', 'online', 'online')
        self.wait(owner.tick(self.v1, '2026-10-18T10:02:00Z'))
        before, during, after = self.all_windows()
        self.assertEqual(before['closeReason'], 'publication-loss')
        self.assertEqual(during['closeReason'], 'publication-recovered')
        self.assertIsNone(after['closeReason'])
        self.assertEqual(self.counts(during)['refresh']['unavailable:unknown-loss'], 1)
        self.assertEqual(self.counts(after), {})  # the recovered window starts from zero, never merged

    def test_a_version_change_closes_the_window(self):
        owner = self.owner()
        self.wait(owner.start(self.v1, '2026-10-18T10:00:00Z'))
        v2 = self.windows.Versions(comms='aaaa1111', env='bbbb2222')
        self.wait(owner.tick(v2, '2026-10-18T10:00:30Z'))
        old, new = self.all_windows()
        self.assertEqual(old['closeReason'], 'version-change')
        self.assertEqual(new['versions']['env'], 'bbbb2222')

    def test_a_lost_flush_closes_the_window_it_was_flushing(self):
        owner = self.owner()
        self.wait(owner.start(self.v1, '2026-10-18T10:00:00Z'))
        self.recorder.record('refresh', 'online', 'online')
        connect = owner._connect
        async def unavailable(): raise OSError('database unavailable')
        owner._connect = unavailable
        with self.assertRaises(OSError):
            self.wait(owner.tick(self.v1, '2026-10-18T10:00:30Z'))
        owner._connect = connect
        self.recorder.record('refresh', 'online', 'online')
        self.wait(owner.tick(self.v1, '2026-10-18T10:01:00Z'))
        broken, new = self.all_windows()
        self.assertEqual(broken['closeReason'], 'flush-lost')
        self.assertEqual(self.counts(broken)['refresh']['observed'], 1, 'the lost batch is gone, and the close says so')

    def test_a_refused_rotation_commit_keeps_the_durable_window_and_the_next_batch_lands(self):
        # R1: the window opened inside a transaction that then failed never existed, so the owner must not adopt it.
        from service.db import get_db
        refusals = [1]
        class RefuseOneCommit:
            def __init__(self, connection): self.connection = connection
            async def execute(self, sql, params=()): return await self.connection.execute(sql, params)
            async def commit(self):
                if refusals:
                    refusals.pop()
                    raise OSError('injected commit refusal')
                await self.connection.commit()
            async def close(self): await self.connection.close()
        owner = self.windows.ShadowWindows(self.recorder)
        self.wait(owner.start(self.v1, '2026-10-18T10:00:00Z'))
        owner._connect = lambda: _wrap(get_db, RefuseOneCommit)
        self.recorder.record('refresh', reason='unknown-loss')  # this flush rotates: a loss begins
        with self.assertRaisesRegex(OSError, 'injected'):
            self.wait(owner.tick(self.v1, '2026-10-18T10:00:30Z'))
        self.recorder.record('refresh', 'online', 'online')  # a nonempty next batch
        self.wait(owner.tick(self.v1, '2026-10-18T10:01:00Z'))
        first, second = self.all_windows()
        self.assertEqual(first['closeReason'], 'flush-lost')
        self.assertEqual(self.counts(first)['refresh']['observed'], 1, 'the next batch landed in the durable window')
        self.assertIsNone(second['closedAt'])

    def test_a_quiet_flush_does_not_end_a_loss_only_a_completed_comparison_does(self):
        # R2: no observation is no evidence; the loss stays in force until a comparison completes.
        owner = self.owner()
        self.wait(owner.start(self.v1, '2026-10-18T10:00:00Z'))
        self.recorder.record('refresh', reason='unknown-loss')
        self.wait(owner.tick(self.v1, '2026-10-18T10:00:30Z'))
        self.wait(owner.tick(self.v1, '2026-10-18T10:01:00Z'))  # quiet
        self.recorder.record('refresh', reason='missing-row')  # not a completed comparison either
        self.wait(owner.tick(self.v1, '2026-10-18T10:01:30Z'))
        self.recorder.record('refresh', 'online', 'online')
        self.wait(owner.tick(self.v1, '2026-10-18T10:02:00Z'))
        before, during, after = self.all_windows()
        self.assertEqual(before['closeReason'], 'publication-loss')
        self.assertEqual((during['closeReason'], during['closedAt']), ('publication-recovered', '2026-10-18T10:02:00Z'))
        self.assertIsNone(after['closedAt'])

    def test_an_observation_after_the_drain_belongs_to_the_window_being_opened(self):
        # R4: only what was recorded before the drain may disappear.
        owner = self.owner()
        self.recorder.record('refresh', 'blocked', 'online')  # before the drain: in no window
        original = self.windows.ShadowWindows._open
        async def open_with_a_concurrent_observation(db, versions, at):
            self.recorder.record('engine-status', 'online', 'online')  # after the drain, before the commit
            return await original(db, versions, at)
        with patch.object(self.windows.ShadowWindows, '_open', staticmethod(open_with_a_concurrent_observation)):
            self.wait(owner.start(self.v1, '2026-10-18T10:00:00Z'))
        self.wait(owner.tick(self.v1, '2026-10-18T10:00:30Z'))
        [window] = self.all_windows()
        self.assertEqual(self.counts(window), {'engine-status': {
            'observed': 1, 'evaluated': 1, 'partialCompared': 1, 'partialAgreed': 1}})

    def test_the_loop_retries_an_opening_that_failed_and_shutdown_survives_a_failed_task(self):
        # R3: a transient failure must not end the loop, and a task that did fail must not stop shutdown.
        calls = []
        class FlakyWindows:
            async def start(self, versions, at):
                calls.append('start')
                if calls.count('start') == 1:
                    raise OSError('database not ready')
            async def tick(self, versions, at):
                calls.append('tick')
        async def scenario():
            task = asyncio.create_task(self.windows.run_periodic(self.v1, FlakyWindows(), every=0.01))
            async def until_ticked():
                while 'tick' not in calls:
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(until_ticked(), 5)  # a loop that died on the first failure never ticks
            await self.windows.stop(task)
            self.assertTrue(task.cancelled(), 'stop cancels a running loop')
            async def failed(): raise RuntimeError('the loop died')
            dead = asyncio.create_task(failed())
            await asyncio.sleep(0)
            await self.windows.stop(dead)  # must return: the rest of shutdown runs after it
            return 'shutdown continued'
        self.assertEqual(self.wait(scenario()), 'shutdown continued')
        self.assertEqual(calls[:3], ['start', 'start', 'tick'])

    def test_the_route_returns_the_newest_windows_first(self):
        owner = self.owner()
        self.wait(owner.start(self.v1, '2026-10-18T10:00:00Z'))
        self.wait(owner.tick(self.windows.Versions(comms='cccc3333'), '2026-10-18T10:00:30Z'))
        body = self._client.get('/api/v1/agent-state/shadow-windows?limit=1').json()
        self.assertEqual([w['versions']['comms'] for w in body['windows']], ['cccc3333'])


async def _wrap(connect, proxy):
    return proxy(await connect())
