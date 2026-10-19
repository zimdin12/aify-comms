"""S2c slice 6, B6a: the receiver takes aify-env's build and turn age, and every window knows the env builds and fold
reads behind its counts. Each test names the design obligation it holds (rev3, D1-D3, option iii)."""
import asyncio
from unittest import mock

from service import clock
from service.api_core import comms_view as cv, delivery_turns as dt, partial_status_shadow as shadow
from service.api_core import busy_consumers as bc, shadow_provenance as prov, shadow_windows as windows
from service.api_core.claim_gating import BusyDecision
from service.tests import test_agent_state_receiver as rx, test_shadow_evidence as e1
from service.tests._base import FastApiTestCase

ROW = {'id': 'a', 'machine_id': 'm', 'status': 'online'}
NOW = '2026-10-19T00:10:00Z'
FRESH = '2026-10-19T00:09:50Z'
AT_EPOCH = 1792368600.0  # NOW


def record(build, busy_if=(False, False)):
    return shadow.Record('a', 'L', 'idle', (), (False, 0, 1), busy_if, ('running', 'yes'), b'{}',
                         (1, 'boot', 1, FRESH, build))


def projection(build, publication=1, kind='snapshot', rows=None):
    return shadow.Projection(('m', 'i1'), kind, 1, FRESH, (record(build),) if rows is None else rows, (), 'boot',
                             publication, build)


class ReceiverTest(FastApiTestCase):
    """D3 and ageMs at the real route, on this test's own mirror, recorder and fold: the route feeds the process
    globals, and a build left there would seed every later window in this worker."""
    post = rx.AgentStateReceiverTest.post

    def setUp(self):
        super().setUp()
        for p in (mock.patch.object(shadow, 'mirror', shadow.Mirror()), mock.patch.object(shadow, 'recorder',
                  shadow.Recorder()), mock.patch.object(dt, 'FOLD', dt.DeliveryTurns())):
            p.start(); self.addCleanup(p.stop)

    def turned(self, **turn):
        record_ = rx.row()
        record_['turn'] = dict(record_['turn'], **turn)
        return record_

    def test_d3_a_build_and_an_age_are_taken_present_or_absent_for_every_kind(self):
        bodies = [rx.body(), rx.body(build='env-1'), rx.body(agents=[self.turned(ageMs=650)], publication=2),
                  rx.body(kind='changes', publication=3, build='env-1', removed=[]),
                  rx.body(kind='unavailable', publication=4), rx.body(kind='unavailable', publication=5, build='env-1')]
        generation = 10
        for value in bodies:
            generation += 1
            value['generation'] = generation
            with self.subTest(kind=value['kind'], build=value.get('build')):
                self.assertEqual(self.post(value).status_code, 204)

    def test_d3_an_unknown_build_is_omitted_and_a_present_null_or_bad_one_refused(self):
        for bad in (None, '', 'x' * 201, 7):
            with self.subTest(build=str(bad)):
                self.assertEqual(self.post(rx.body(build=bad)).status_code, 422)
        for age in (-1, 1.5, '5', True):
            with self.subTest(age=str(age)):
                self.assertEqual(self.post(rx.body(agents=[self.turned(ageMs=age)])).status_code, 422)
        self.assertEqual(self.post(rx.body(agents=[self.turned(ageMz=1)])).status_code, 422, 'still no unknown field')

    def test_d3_unavailable_keeps_the_data_build_and_a_merged_record_its_own(self):
        mirror, recorder = shadow.Mirror(), shadow.Recorder()
        with mock.patch.object(shadow, 'mirror', mirror), mock.patch.object(shadow, 'recorder', recorder):
            self.assertEqual(self.post(rx.body(build='env-A')).status_code, 204)
            other = dict(rx.row('beta'), lifetime='real-beta')
            self.assertEqual(self.post(rx.body(kind='changes', publication=2, build='env-B', agents=[other],
                                               removed=[])).status_code, 204)
            self.assertEqual(self.post(rx.body(kind='unavailable', publication=3, build='env-C')).status_code, 204)
            [publisher] = mirror.view().values()
            self.assertEqual(publisher.build, 'env-B', 'an unavailable publication never changes the data build')
            self.assertEqual({r.agent_id: prov.origin_build(r) for r in publisher.rows},
                             {'alpha': 'env-A', 'beta': 'env-B'})
            builds, _, _ = recorder.take_provenance()
            self.assertEqual(builds, {'env-A', 'env-B'}, 'each applied data publication put its build in effect')

    def test_d2_the_estimate_is_never_before_the_true_start_at_whole_second_ingress(self):
        # True start 100.250 s after the stamp's second; body built at +0.900 (age 650), received at +0.950: the
        # route stamps the whole second, so the estimate must add the stamp's resolution back.
        fold = dt.DeliveryTurns()
        closed = self.turned(open=False)
        opened = self.turned(open=True, startedAtUs=1_700_000_100_250_000, lastEventAtUs=1_700_000_100_250_000,
                             ageMs=650)
        with mock.patch.object(dt, 'FOLD', fold):
            self.assertEqual(self.post(rx.body(agents=[closed]), at='2026-10-07T09:00:00Z').status_code, 204)
            self.assertEqual(self.post(rx.body(agents=[opened], publication=2), at='2026-10-07T09:01:40Z').status_code,
                             204)
        [(key, estimate)] = fold._estimates.items()
        stamp = clock.iso_to_epoch('2026-10-07T09:01:40Z')
        self.assertEqual((estimate.minimum, estimate.authoritative), (stamp + 1.0 - 0.650, True))
        self.assertGreaterEqual(estimate.minimum, stamp + 0.250, 'never before the true start (stamp + .250)')

    def test_d2_a_delayed_body_only_moves_the_estimate_later(self):
        fold = dt.DeliveryTurns()
        opened = self.turned(open=True, startedAtUs=1, lastEventAtUs=1, ageMs=650)
        with mock.patch.object(dt, 'FOLD', fold):
            self.post(rx.body(agents=[self.turned(open=False)]), at='2026-10-07T09:00:00Z')
            self.post(rx.body(agents=[opened], publication=2), at='2026-10-07T09:01:45Z')  # the same body, 5 s late
        [estimate] = fold._estimates.values()
        self.assertEqual(estimate.minimum, clock.iso_to_epoch('2026-10-07T09:01:45Z') + 1.0 - 0.650)


class _Windows(e1._Evidence):
    def setUp(self):
        super().setUp()
        self.fold = dt.DeliveryTurns()
        p = mock.patch.object(dt, 'FOLD', self.fold)
        p.start(); self.addCleanup(p.stop)
        self.owner = windows.ShadowWindows(self.recorder)
        self.now = NOW

    def apply(self, value):
        """What the receiver does with an applied publication: feed the mirror, then note what it put in effect."""
        self.mirror.feed(value)
        self.recorder.provenance.applied(value)

    def word(self):
        shadow.observe(shadow.bind(), ROW, e1.inputs(), 'online', 'refresh')

    def window(self, window_id):
        page = self.page(window_id)
        return page['window'], {(r['owner'], r['variant']): r['gaps'] for r in page['coverage']}


class WindowBuildsTest(_Windows):
    def test_d1_old_and_new_observations_before_one_flush_make_the_window_mixed(self):
        self.apply(projection('A'))
        self.wait(self.owner.start(e1.V, e1.at(0)))
        self.word()
        self.wait(self.owner.tick(e1.V, e1.at(10)))
        self.apply(projection('B', publication=2))
        self.word()
        self.wait(self.owner.tick(e1.V, e1.at(20)))
        first, gaps = self.window(1)
        self.assertEqual((first['envBuilds'], first['closeReason'], gaps[(shadow.WORD, '-')]),
                         ('["A","B"]', 'version-change', ['env-version']))
        self.wait(self.owner.tick(e1.V, e1.at(30)))
        reopened, gaps = self.window(2)
        self.assertEqual((reopened['envBuilds'], gaps[(shadow.WORD, '-')]), ('["B"]', []), 'reopened coherent')

    def test_d1_a_build_that_reverts_between_ticks_still_shows(self):
        self.apply(projection('A'))
        self.wait(self.owner.start(e1.V, e1.at(0)))
        self.apply(projection('B', publication=2))
        self.apply(projection('A', publication=3))
        self.wait(self.owner.tick(e1.V, e1.at(10)))
        self.assertEqual(self.window(1)[0]['envBuilds'], '["A","B"]')

    def test_d1_unknown_then_known_is_mixed_and_a_quiet_same_build_publication_is_not(self):
        self.apply(projection(None))
        self.wait(self.owner.start(e1.V, e1.at(0)))
        self.apply(projection('A', publication=2))
        self.wait(self.owner.tick(e1.V, e1.at(10)))
        self.assertEqual(self.window(1)[0]['envBuilds'], '["A","unknown"]')
        self.wait(self.owner.tick(e1.V, e1.at(20)))  # window 2 opens on A
        for publication in (3, 4):
            self.apply(projection('A', publication=publication))  # quiet: same build, new publication numbers
            self.wait(self.owner.tick(e1.V, e1.at(20 + publication)))
        quiet, gaps = self.window(2)
        self.assertEqual((quiet['envBuilds'], quiet['closedAt'], gaps[(shadow.WORD, '-')]), ('["A"]', None, []))

    def test_d1_a_merged_record_keeps_its_older_origin_build_in_effect(self):
        self.apply(projection('A'))
        newer = shadow.Record('b', 'L', 'idle', (), None, None, ('running', 'yes'), b'{}', (1, 'boot', 2, FRESH, 'B'))
        self.apply(shadow.Projection(('m', 'i1'), 'changes', 1, FRESH, (newer,), (), 'boot', 2, 'B'))
        [publisher] = self.mirror.view().values()
        self.assertEqual((publisher.build, prov.builds_of(shadow.bind())), ('B', {'A', 'B'}),
                         'a B publisher still holds the A record a changes publication left in place')
        self.wait(self.owner.start(e1.V, e1.at(0)))
        self.assertEqual(self.window(1)[0]['envBuilds'], '["A","B"]', 'and a window opening now is seeded with both')

    def test_d1_unavailable_shadow_error_and_loss_contribute_their_bindings(self):
        self.apply(projection('A'))
        self.recorder.take_provenance()
        binding = shadow.bind()
        self.recorder.record_owner('busy', 'strict', 'claim', False, None, 'delivery-turn', binding=binding, fold=True)
        broken = shadow.Recorder()
        real = broken.record_owner

        def failing_once(*args, **kwargs):
            broken.record_owner = real
            raise RuntimeError('recorder broken once')
        broken.record_owner = failing_once
        with mock.patch.object(shadow, 'recorder', broken):
            cv.record_answer('busy', 'strict', 'claim', False, (True, None), binding=binding, fold=True)
        self.assertEqual(broken.take_provenance()[0], {'A'}, 'the shadow-error fallback records the same binding')
        self.recorder.record_owner('busy', 'strict', 'claim', False, None, 'shadow-error', binding=prov.LOST)
        builds, fold, _ = self.recorder.take_provenance()
        self.assertEqual((builds, fold), ({'A', 'unknown'}, {'busy'}))


class _Held(_Windows):
    """D1's late binding: an A frame held across a B publication, a close and a reopen, recorded afterwards."""

    def setUp(self):
        super().setUp()
        self.fold._turns[('m', 'a')] = dt.DeliveryTurn(True, 'r', AT_EPOCH - 5000, AT_EPOCH - 10, 'bridge-1')
        self.entered, self.release = None, None

    async def held(self, db, agent_id, owner):
        self.entered.set()
        await self.release.wait()
        return 'negative'  # with an idle record and an expired turn: C6 holds nothing, as today: an AGREEMENT

    def run_case(self, b_before_capture):
        async def scenario():
            self.entered, self.release = asyncio.Event(), asyncio.Event()
            self.apply(projection('A'))
            await self.owner.start(e1.V, e1.at(0))
            if b_before_capture:
                self.apply(projection('B', publication=2))
            with mock.patch.object(cv, 'turn_lease_renewal', side_effect=self.held):
                task = asyncio.create_task(cv.observe_busy(None, ROW, BusyDecision(False), 'claim'))
                await self.entered.wait()
                if not b_before_capture:
                    self.apply(projection('B', publication=2))
                await self.owner.tick(e1.V, e1.at(10))  # window 1 closes on B; window 2 opens seeded B
                self.release.set()
                await task
            await self.owner.tick(e1.V, e1.at(20))
        self.wait(scenario())
        busy = self.recorder.report()['byPurpose']['busy']['variants']['strict']['counts']
        self.assertEqual(busy['partialAgreed'], 1, 'the held observation recorded an agreement')

class HeldRenewalTest(_Held):
    def test_d1_an_a_frame_recorded_after_the_reopen_makes_the_receiving_window_mixed(self):
        self.run_case(b_before_capture=False)
        window, gaps = self.window(2)
        self.assertEqual((window['envBuilds'], gaps[('busy', 'strict')][0]), ('["A","B"]', 'env-version'))

    def test_the_twin_captured_after_b_leaves_the_receiving_window_coherent(self):
        self.run_case(b_before_capture=True)
        window, gaps = self.window(2)
        self.assertEqual((window['envBuilds'], 'env-version' in gaps[('busy', 'strict')]), ('["B"]', False))


class FoldContextTest(_Held):
    """Option iii: fold-derived owners never qualify, whatever the builds say, through a clean close and expiry."""

    def test_iii_a_fold_derived_agreement_in_a_b_only_window_is_refused_through_close_and_expiry(self):
        self.run_case(b_before_capture=True)  # window 2: B only, a fold-derived busy agreement in it
        asyncio.run(bc.observe_stranded(None, 'a', 'r', False))  # the fold only, with no env view at all
        self.wait(self.owner.tick(e1.V2, e1.at(30)))  # a clean close: version-change on comms
        window, gaps = self.window(2)
        self.assertEqual((window['envBuilds'], window['closeReason']), ('["B"]', 'version-change'))
        self.assertEqual((gaps[('busy', 'strict')], gaps[(shadow.WORD, '-')]), (['fold-context'], []))
        self.assertEqual(gaps[('stranded-reply guard (B26)', '-')], ['fold-context'], 'stranded, with no binding')
        self.assertEqual(self.wait(windows.expire_windows('2026-11-03T10:00:00Z')), 2)
        expired, gaps = self.window(2)
        self.assertEqual((expired['expired'], expired['envBuilds'], gaps[('busy', 'strict')]),
                         (True, '["B"]', ['fold-context']))

    def test_iii_a_window_without_its_fold_record_refuses_every_fold_owner(self):
        self.assertEqual(prov.fold_unverified(None), frozenset(prov.FOLD_OWNERS))
        self.assertEqual(prov.fold_unverified('{"x": 1}'), frozenset(prov.FOLD_OWNERS))
        self.assertEqual(prov.fold_unverified('[]'), frozenset())
