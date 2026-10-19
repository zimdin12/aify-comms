"""S2c slice 6, B6a: the receiver takes aify-env's build and turn age, and every window knows the env builds and fold
reads behind its counts. Each test names the design obligation it holds (rev3, D1-D3, option iii)."""
import asyncio
from unittest import mock

from service import clock
from service.api_core import comms_view as cv, delivery_turns as dt, partial_status_shadow as shadow
from service.api_core import busy_consumers as bc, shadow_provenance as prov, shadow_windows as windows
from service.api_core import shadow_evidence as ev, shadow_evidence_store as store
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

    def test_d1_an_evaluation_that_lost_its_binding_is_unknown_and_the_reminder_keeps_its_own(self):
        self.assertIs(cv.binding_of(None), prov.LOST, 'a failed busy evaluation is unknown, never consulted-nothing')
        self.apply(projection('A'))
        self.recorder.take_provenance()
        self.recorder.provenance._builds.clear()  # only what the reminder itself contributes
        asyncio.run(bc.observe_reminder(None, 'a', 'r', False, blocked=False, occupied=False, agent_row=ROW))
        self.assertEqual(self.recorder.take_provenance()[:2], ({'A'}, {'reminder skip (B21)'}))

    def test_the_evidence_census_names_each_publishers_and_records_build(self):
        self.apply(projection('A'))
        env, missing = ev.census(shadow.bind(), ('m', 'a'))
        self.assertEqual(([p['envBuild'] for p in env['publishers']], [r['origin']['envBuild'] for r in env['records']],
                          missing), (['A'], ['A'], []))

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
        expired, expired_gaps = self.window(2)
        self.assertEqual((expired['expired'], expired['envBuilds'], expired['foldUnverified']),
                         (True, window['envBuilds'], window['foldUnverified']), 'the tombstone keeps both records')
        self.assertEqual(expired_gaps, gaps, 'and answers the same refusals')

    def test_a_named_gap_is_never_complete_and_unknown_alone_is_never_coherent(self):
        totals = {('busy', 'strict', 'claim'): dict(disagreed=1, completeRows=1, incompleteRows=0, missing={})}

        def site(**kwargs):
            row = next(r for r in store.coverage(totals, frozenset({'busy'}), True, **kwargs) if r['variant'] == 'strict')
            return row['gaps'], row['sites']['claim']['complete']
        self.assertEqual(site(), ([], True), 'the control: no gap, complete')
        self.assertEqual(site(coherent=False), (['env-version'], False))
        self.assertEqual(site(fold=frozenset({'busy'})), (['fold-context'], False))
        self.assertEqual([prov.env_coherent(s) for s in ('["A"]', '["unknown"]', '["A","B"]', None)],
                         [True, False, False, False])

    def test_each_interval_starts_with_the_builds_still_in_effect(self):
        provenance = prov.Provenance()
        provenance.applied(projection('A'))
        in_effect = shadow.Binding(shadow.MappingProxyType({('m', 'i1'): shadow.Publisher(
            1, FRESH, True, False, (record('A'),), build='A')}), NOW)
        self.assertEqual(provenance.take(in_effect)[0], {'A'})
        self.assertEqual(provenance.take(in_effect)[0], {'A'}, 'a quiet interval still carries the builds in effect')

    def test_iii_a_window_without_its_fold_record_refuses_every_fold_owner(self):
        self.assertEqual(prov.fold_unverified(None), frozenset(prov.FOLD_OWNERS))
        self.assertEqual(prov.fold_unverified('{"x": 1}'), frozenset(prov.FOLD_OWNERS))
        self.assertEqual(prov.fold_unverified('[]'), frozenset())


class PublisherLossTest(_Windows):
    """R1: a publication the receiver could not apply leaves its publisher lost, and that loss stays unknown in every
    seed until a later publication of it applies."""
    post = rx.AgentStateReceiverTest.post

    def run_case(self, recover):
        self.post(rx.body(build='env-A'))
        self.wait(self.owner.start(e1.V, e1.at(0)))
        with mock.patch.object(shadow, 'prepare', side_effect=RuntimeError('unreadable publication')):
            self.post(rx.body(build='env-A', publication=2))  # committed, but the mirror could not take it
        if recover:
            self.post(rx.body(build='env-A', publication=3))  # a snapshot that applies: the publisher recovers
        self.wait(self.owner.tick(e1.V, e1.at(10)))  # the loss interval closes window 1; window 2 opens
        shadow.observe(shadow.bind(), (' Host ', 'alpha'), e1.inputs(), 'online', 'refresh')  # reads no fold
        self.wait(self.owner.tick(e1.V, e1.at(20)))  # quiet: no recovery
        return self.window(2)

    def test_r1_an_unrecovered_publisher_loss_keeps_the_reopened_window_unknown(self):
        window, gaps = self.run_case(recover=False)
        self.assertEqual((window['envBuilds'], gaps[(shadow.WORD, '-')]), ('["env-A","unknown"]', ['env-version']))

    def test_the_twin_that_recovered_reopens_coherent(self):
        window, gaps = self.run_case(recover=True)
        self.assertEqual((window['envBuilds'], gaps[(shadow.WORD, '-')]), ('["env-A"]', []))


class FoldRecordTest(_Windows):
    """R2: a fold record that cannot be read is never repaired by a later append, and an owner it does not know makes
    it unreadable; valid records are read exactly."""
    CASES = ((None, ['fold-context'], ['fold-context']), ('{"x": 1}', ['fold-context'], ['fold-context']),
             ('["busy-typo"]', ['fold-context'], ['fold-context']), ('[]', [], []),
             ('["busy"]', ['fold-context'], []))

    def store(self, window_id, value):
        import sqlite3
        from contextlib import closing
        with closing(sqlite3.connect(self._db_path)) as db:
            db.execute('UPDATE status_shadow_windows SET fold_unverified = ? WHERE id = ?', (value, window_id))
            db.commit()

    def test_r2_every_record_survives_a_quiet_flush_a_clean_close_and_expiry_as_stored(self):
        self.apply(projection('A'))  # one known build throughout: the fold record is the only gap under test
        self.wait(self.owner.start(e1.V, e1.at(0)))
        versions = (e1.V, e1.V2)
        for index, (stored, _, _) in enumerate(self.CASES):
            self.store(index + 1, stored)
            self.wait(self.owner.tick(versions[index % 2], e1.at(10 * index + 5)))  # a quiet flush into it
            self.wait(self.owner.tick(versions[(index + 1) % 2], e1.at(10 * index + 9)))  # a clean close
        self.assertEqual(self.wait(windows.expire_windows('2026-11-03T10:00:00Z')), len(self.CASES))
        for index, (stored, busy, claim) in enumerate(self.CASES):
            with self.subTest(stored=str(stored)):
                window, gaps = self.window(index + 1)
                self.assertEqual((window['expired'], window['closeReason'], window['foldUnverified']),
                                 (True, 'version-change', stored), 'never repaired, through expiry')
                self.assertEqual((gaps[('busy', 'strict')], gaps[('claim', '-')]), (busy, claim))
