"""The stale-turn ceiling beside today's (status-switch B6c).

Four kinds of witness: the named histories of the two pure sides; the parity of today's recomputed strict arm with the
delivery decision wherever the renewal is not verified, over every hold-law specimen; the observer's counts, evidence
and containment; and the two real sites, where a busy row is observed and a row that is not busy is not.
"""
import asyncio
import json
import time
import unittest
from unittest import mock

from service.api_core import comms_view as cv, delivery_turns as dt, partial_status_shadow as shadow
from service.api_core import shadow_evidence, shadow_provenance, stale_turn_ceiling as stc
from service.api_core.claim_gating import BusyDecision, turn_busy_decision
from service.api_core.liveness import TURN_BUSY_BACKSTOP_SECONDS as CEILING_S
from service.db import get_db
from service.tests import test_p7_witness_delivery_hold as p7
from service.tests._base import FastApiTestCase
from service.tests.delivery_witnesses import HOLD_LAW, INPUT_ADAPTER
from service.tests.test_busy_consumers import CLOSED, FRESH, _Sites

NK = dt.NOT_KNOWN
NOW = 1_800_000_000.0


def iso(epoch):
    return time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime(epoch))


def receipt(started_ago=100, touched_ago=None, busy=1, renewal='negative', read='row'):
    """What turn_busy_decision read: a busy row anchored `started_ago` seconds before NOW (None: no anchor)."""
    return dict(read=read, turn_busy=busy, turn_started_at='' if started_ago is None else iso(NOW - started_ago),
                turn_updated_at=iso(NOW - (touched_ago if touched_ago is not None else 5)), turn_bridge_id='b',
                nowEpoch=NOW, owner='b', renewal=renewal)


def holder(expiry, freshness='stale', open_=True):
    return cv.Holder(freshness, (True, True), (open_, 5, 5), expiry, NK, ('m', 'i1'), 'L')


def frame(*holders, at=NOW, others_unknown=False):
    return cv.Frame(at, tuple(holders), others_unknown, dt.CLOSED)


class TodayTest(unittest.TestCase):
    def test_begun_within_and_past_the_ceiling(self):
        self.assertEqual(stc.today_ceiling(receipt(100))[0], True)
        self.assertEqual(stc.today_ceiling(receipt(2000))[0], False)

    def test_the_boundary_is_inclusive_today(self):
        self.assertEqual(stc.today_ceiling(receipt(CEILING_S))[0], True)
        self.assertEqual(stc.today_ceiling(receipt(CEILING_S + 1))[0], False)

    def test_a_future_anchor_is_a_law_class_today_false_candidate_true(self):
        self.assertEqual(stc.today_ceiling(receipt(-1))[0], False, 'today refuses a negative age')
        self.assertEqual(stc.ceiling_candidate(frame(holder(NOW + 1 + CEILING_S)), NOW)[:2], (True, None))

    def test_a_verified_renewal_does_not_extend_the_strict_arm(self):
        self.assertEqual(stc.today_ceiling(receipt(2000, touched_ago=5, renewal='verified'))[0], False)

    def test_an_anchorless_row_names_the_fallback_column(self):
        holds, anchor, column = stc.today_ceiling(receipt(None, touched_ago=60))
        self.assertEqual((holds, anchor, column), (True, NOW - 60, 'turn_updated_at'))
        self.assertEqual(stc.today_ceiling(receipt(100))[1:], (NOW - 100, 'turn_started_at'))

    def test_no_busy_row_is_no_ceiling_decision(self):
        for r in (receipt(busy=0), dict(read='no-row'), dict(read='read-error')):
            self.assertIsNone(stc.today_ceiling(r), r)


class CandidateTest(unittest.TestCase):
    def test_begun_within_and_past_the_estimate(self):
        self.assertEqual(stc.ceiling_candidate(frame(holder(NOW + 1700)), NOW)[:2], (True, None))
        self.assertEqual(stc.ceiling_candidate(frame(holder(NOW - 200)), NOW)[:2], (False, None))

    def test_the_candidate_is_exclusive_at_its_expiry(self):
        self.assertEqual(stc.ceiling_candidate(frame(holder(NOW)), NOW)[:2], (False, None))

    def test_a_fresh_record_is_judged_by_its_expiry_too(self):
        self.assertEqual(stc.ceiling_candidate(frame(holder(NOW - 1, freshness='fresh')), NOW)[:2], (False, None))

    def test_it_is_judged_at_the_receipts_instant_not_the_frames(self):
        self.assertEqual(stc.ceiling_candidate(frame(holder(NOW + 50), at=NOW + 100), NOW)[:2], (True, None))

    def test_each_unknown_names_its_reason_and_is_never_false(self):
        cases = {
            'record-knowledge': (frame(holder(NOW + 1), at=None), frame(holder(NOW + 1), others_unknown=True),
                                 frame(cv.Holder('unknown'))),
            'ambiguous-association': (frame(holder(NOW + 1), holder(NOW + 1)),),
            'unpaired': (frame(), frame(holder(NOW + 1, open_=False)), frame(cv.Holder('stale', None, None))),
            'strict-expiry': (frame(holder(NK)),),
        }
        for reason, frames in cases.items():
            for f in frames:
                self.assertEqual(stc.ceiling_candidate(f, NOW)[:2], (NK, reason), f)


class ParityTest(FastApiTestCase):
    """Today's recomputed strict arm IS the delivery decision wherever the renewal is not verified."""

    def test_every_hold_law_specimen(self):
        async def decide(agent_id):
            db = await get_db()
            try:
                return await turn_busy_decision(db, agent_id)
            finally:
                await db.close()
        compared = 0
        for case in HOLD_LAW:
            agent = f'p-{case.spec.name}'.replace('/', '-')
            self.client.post('/api/v1/agents', json={'agentId': agent, 'role': 'coder'}).raise_for_status()
            INPUT_ADAPTER.apply(self._db_path, agent, case.spec)
            decision = asyncio.run(decide(agent))
            today = stc.today_ceiling(decision.receipt)
            if decision.renewal == 'verified' or today is None:
                self.assertFalse(today is None and decision.holds, f'{agent}: a hold with no ceiling decision')
                continue
            compared += 1
            self.assertEqual(today[0], decision.holds, agent)
        self.assertGreaterEqual(compared, 3, 'CONTROL: the specimens reached the strict arm')


class ObserverTest(unittest.TestCase):
    def setUp(self):
        self.recorder = shadow.Recorder()
        p = mock.patch.object(shadow, 'recorder', self.recorder)
        p.start(); self.addCleanup(p.stop)

    def observe(self, r, f=None, site='claim'):
        decision = BusyDecision(True, 'b', 'negative', r)
        busy = cv.BusyObservation({}, cv.Basis(decision, f, None, 'not-needed', ('m', 'a'))) if f else None
        stc.observe_ceiling(decision, busy, site)

    def owner(self):
        return self.recorder.report()['byPurpose'][stc.CEILING]['variants']['-']

    def test_a_late_receipt_disagrees_and_keeps_both_anchors(self):
        # Today's row was stamped 20 s after the env's estimate: today still holds, the candidate has expired.
        self.observe(receipt(CEILING_S - 10), frame(holder(NOW - 10)))
        self.assertEqual(self.owner()['disagreementsByKey'], [dict(site='claim', today='true', candidate='false',
                                                                   count=1)])
        [kept], gaps = self.recorder.evidence.take()
        self.assertEqual(gaps, {})
        body = json.loads(kept.payload)
        self.assertEqual((kept.owner, body['today'], body['candidate']), (stc.CEILING, True, False))
        self.assertEqual({k: body['comms'][k] for k in ('todayAnchor', 'anchorColumn', 'todayExpiry')},
                         dict(todayAnchor=NOW - CEILING_S + 10, anchorColumn='turn_started_at', todayExpiry=NOW + 10))
        inputs = body['candidateInputs']
        self.assertEqual((inputs['candidateExpiry'], inputs['holderFreshness'], inputs['frameAt']),
                         (NOW - 10, 'stale', NOW))
        self.assertIn('busy', body['comms'], 'the busy basis travels with the item')

    def test_an_agreement_keeps_no_item_and_counts(self):
        self.observe(receipt(100), frame(holder(NOW + 1700)))
        self.assertEqual(self.owner()['counts']['partialAgreed'], 1)
        self.assertEqual(self.recorder.evidence.take()[0], ())

    def test_a_row_that_is_not_busy_records_nothing(self):
        self.observe(receipt(busy=0), frame(holder(NOW + 1)))
        self.observe(receipt(100), frame(holder(NOW + 1)))
        self.assertEqual(self.owner()['counts']['observed'], 1, 'CONTROL: the busy row is observed, the idle one not')

    def test_a_missing_or_unreadable_receipt_or_basis_is_unavailable(self):
        self.observe(None, frame(holder(NOW + 1)))
        unreadable = receipt(100)
        del unreadable['nowEpoch']
        self.observe(unreadable, frame(holder(NOW + 1)))
        self.observe(receipt(100), None)
        self.assertEqual(self.owner()['unavailableByReason'], {'claim': {'comms-receipt': 2, 'shadow-error': 1}})

    def test_an_observation_marks_a_fold_read(self):
        self.observe(receipt(100), frame(holder(NOW + 1)))
        self.assertIn(stc.CEILING, self.recorder.provenance._fold)

    def test_a_missing_or_malformed_fold_record_refuses_it_by_name(self):
        for stored in (None, '{"x": 1}'):
            self.assertIn('stale-turn ceiling', shadow_provenance.fold_unverified(stored), stored)

    def test_it_owns_its_evidence(self):
        self.assertIn('stale-turn ceiling', shadow_evidence.EVIDENCE_OWNERS)

    def test_a_failure_before_the_recording_is_contained_and_counted_once(self):
        class Broken:
            holds = True

            @property
            def receipt(self):
                return receipt(100)
        busy = mock.Mock()
        type(busy).basis = mock.PropertyMock(side_effect=RuntimeError('basis broken'))
        stc.observe_ceiling(Broken(), busy, 'claim')
        self.assertEqual(self.owner()['unavailableByReason'], {'claim': {'shadow-error': 1}})
        self.assertEqual(self.owner()['counts']['observed'], 1)

    def test_a_failure_is_contained_and_counted(self):
        with mock.patch.object(stc, 'ceiling_candidate', side_effect=RuntimeError('broken')):
            self.observe(receipt(100), frame(holder(NOW + 1)))
        with mock.patch.object(self.recorder, 'record_owner', side_effect=RuntimeError('recorder broken')):
            self.observe(receipt(100), frame(holder(NOW + 1)))
        self.assertEqual(self.owner()['unavailableByReason'], {'claim': {'shadow-error': 1}})


class SendTimeSiteTest(_Sites, p7.SendTimeQueueWitness):
    test_each_specimen_holds_or_releases_the_queue_as_its_law_says = None
    test_an_active_run_or_an_earlier_queued_run_also_holds_the_queue = None

    def test_a_busy_row_is_observed_and_an_idle_one_is_not(self):
        self._assert_waits('s-held', self._queue_send('s-held', FRESH))
        self._assert_woken('s-open', self._queue_send('s-open', CLOSED))
        self.assertEqual(self.owner(stc.CEILING)['bySite']['send-time']['observed'], 1)

    def test_a_failing_observer_changes_nothing(self):
        with mock.patch.object(stc, 'ceiling_candidate', side_effect=RuntimeError('broken')):
            self._assert_waits('s-broken', self._queue_send('s-broken', FRESH))
        self.assertEqual(self.owner(stc.CEILING)['unavailableByReason'], {'send-time': {'shadow-error': 1}})


class ClaimSiteTest(_Sites, p7.ClaimHoldWitness):
    test_each_specimen_holds_or_releases_the_claim_as_its_law_says = None

    def test_a_busy_row_is_observed_and_an_idle_one_is_not(self):
        self._assert_held('c-held', self._claim('c-held', FRESH))
        self._assert_claimed('c-open', self._claim('c-open', CLOSED))
        self.assertEqual(self.owner(stc.CEILING)['bySite']['claim']['observed'], 1)

    def test_a_failing_observer_changes_nothing(self):
        with mock.patch.object(stc, 'ceiling_candidate', side_effect=RuntimeError('broken')):
            self._assert_held('c-broken', self._claim('c-broken', FRESH))
        self.assertEqual(self.owner(stc.CEILING)['unavailableByReason'], {'claim': {'shadow-error': 1}})
