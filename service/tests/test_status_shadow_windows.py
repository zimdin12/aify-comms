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
        with patch.object(self.windows, 'get_db', side_effect=OSError('database unavailable')):
            with self.assertRaises(OSError):
                self.wait(owner.tick(self.v1, '2026-10-18T10:00:30Z'))
        self.recorder.record('refresh', 'online', 'online')
        self.wait(owner.tick(self.v1, '2026-10-18T10:01:00Z'))
        broken, new = self.all_windows()
        self.assertEqual(broken['closeReason'], 'flush-lost')
        self.assertEqual(self.counts(broken)['refresh']['observed'], 1, 'the lost batch is gone, and the close says so')

    def test_the_route_returns_the_newest_windows_first(self):
        owner = self.owner()
        self.wait(owner.start(self.v1, '2026-10-18T10:00:00Z'))
        self.wait(owner.tick(self.windows.Versions(comms='cccc3333'), '2026-10-18T10:00:30Z'))
        body = self._client.get('/api/v1/agent-state/shadow-windows?limit=1').json()
        self.assertEqual([w['versions']['comms'] for w in body['windows']], ['cccc3333'])
