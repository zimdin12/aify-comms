"""The stale-turn ceiling beside today's (status-switch B6c).

Four kinds of witness: the named histories of the two pure sides; the parity of today's recomputed strict arm with the
delivery decision wherever the renewal is not verified, over every hold-law specimen; the observer's counts, evidence
and containment.

THE TWO SITE WITNESSES RETIRED WITH THE 0.9.8 SWITCH (SW5): the queue and claim sites read the operational view and
observe nothing, and this module is comparison-only, deleted in SW7 (plan page 4, item 5).
"""
import asyncio
import json
import time
import unittest
from unittest import mock

from service.api_core import comms_view as cv, delivery_turns as dt, partial_status_shadow as shadow
from service.api_core import shadow_evidence, shadow_provenance, stale_turn_ceiling as stc
from service.api_core import claim_gating as cg
from service.api_core.claim_gating import BusyDecision, turn_busy_decision
from service.api_core.liveness import TURN_BUSY_BACKSTOP_SECONDS as CEILING_S
from service.db import get_db
from service.tests._base import FastApiTestCase
from service.tests.delivery_witnesses import HOLD_LAW, LEGACY_ADAPTER
from service.tests.test_shadow_evidence_e2 import decide_busy
from service.tests import test_delivery_turns as tdt

NK = dt.NOT_KNOWN
NOW = 1_800_000_000.0
# The parent's turn-state reads for one held send and one held claim, measured with the ceiling's wiring removed
# (2026-10-19): two at send time, one at claim. The ceiling adds none.
SEND_TIME_TURN_STATE_READS, CLAIM_TURN_STATE_READS = 2, 1


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
        self.assertEqual(stc.ceiling_population(receipt(100))[0], True)
        self.assertEqual(stc.ceiling_population(receipt(2000))[0], False)

    def test_the_boundary_is_inclusive_today(self):
        self.assertEqual(stc.ceiling_population(receipt(CEILING_S))[0], True)
        self.assertEqual(stc.ceiling_population(receipt(CEILING_S + 1))[0], False)

    def test_a_future_anchor_is_a_law_class_today_false_candidate_true(self):
        self.assertEqual(stc.ceiling_population(receipt(-1))[0], False, 'today refuses a negative age')
        self.assertEqual(stc.ceiling_candidate(frame(holder(NOW + 1 + CEILING_S)), NOW)[:2], (True, None))

    def test_a_verified_renewal_does_not_extend_the_strict_arm(self):
        self.assertEqual(stc.ceiling_population(receipt(2000, touched_ago=5, renewal='verified'))[0], False)

    def test_the_anchor_follows_the_parsed_selection(self):
        holds, anchor, column = stc.ceiling_population(receipt(None, touched_ago=60))
        self.assertEqual((holds, anchor, column), (True, NOW - 60, 'turn_updated_at'))
        malformed = dict(receipt(100, touched_ago=60), turn_started_at='not-a-time')
        self.assertEqual(stc.ceiling_population(malformed), (True, NOW - 60, 'turn_updated_at'),
                         'a nonempty start that does not parse falls back to the touch')
        self.assertEqual(stc.ceiling_population(receipt(100))[1:], (NOW - 100, 'turn_started_at'))

    def test_two_unusable_anchors_invent_no_anchor(self):
        both = dict(receipt(100), turn_started_at='junk', turn_updated_at='')
        self.assertEqual(stc.ceiling_population(both), (False, None, None), "today's real answer, and no number made up")

    def test_known_exclusions_need_no_clock(self):
        for r in (receipt(busy=0), {'read': 'row', 'turn_busy': None}, {'read': 'no-row'}, {'read': 'read-error'}):
            self.assertEqual(stc.ceiling_population(r), stc.EXCLUDED, r)

    def test_what_cannot_be_read_is_eligibility_unknown(self):
        def without_clock(value):
            r = receipt(100)
            r['nowEpoch'] = value
            return r
        cases = [None, 'a string', {'read': 'other'}, {'turn_busy': 1}, dict(receipt(100), turn_busy='busy'),
                 without_clock(None), without_clock(True), without_clock(float('nan')), without_clock(float('inf')),
                 without_clock(str(NOW))]
        for r in cases:
            self.assertEqual(stc.ceiling_population(r), stc.UNKNOWN_ELIGIBILITY, r)
        self.assertEqual(stc.ceiling_population(receipt(100))[0], True, 'CONTROL: an intact busy row is eligible')


class CandidateTest(unittest.TestCase):
    def test_begun_within_and_past_the_estimate(self):
        self.assertEqual(stc.ceiling_candidate(frame(holder(NOW + 1700)), NOW)[:2], (True, None))
        self.assertEqual(stc.ceiling_candidate(frame(holder(NOW - 200)), NOW)[:2], (False, None))

    def test_the_candidate_is_exclusive_at_its_expiry(self):
        self.assertEqual(stc.ceiling_candidate(frame(holder(NOW)), NOW)[:2], (False, None))

    def test_a_fresh_record_is_judged_by_its_expiry_too(self):
        self.assertEqual(stc.ceiling_candidate(frame(holder(NOW - 1, freshness='fresh')), NOW)[:2], (False, None))

    def test_it_is_judged_at_the_receipts_instant_not_the_frames(self):
        # The expiry lies strictly between the receipt's clock and the frame's, so the two clocks disagree.
        self.assertEqual(stc.ceiling_candidate(frame(holder(NOW + 50), at=NOW + 100), NOW)[:2], (True, None))
        self.assertEqual(stc.ceiling_candidate(frame(holder(NOW + 150), at=NOW + 100), NOW)[:2], (True, None),
                         'CONTROL: an expiry past both clocks holds either way')

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


class FoldTrustTest(unittest.TestCase):
    """The candidate trusts only an AUTHORITATIVE estimate: driven through the real fold and the real capture."""

    KEY = ('m', 'i1')

    def setUp(self):
        self.fold, self.mirror = dt.DeliveryTurns(), shadow.Mirror()
        for p in (mock.patch.object(dt, 'FOLD', self.fold), mock.patch.object(shadow, 'mirror', self.mirror),
                  mock.patch.object(shadow, 'clock', return_value=tdt.iso(110))):
            p.start(); self.addCleanup(p.stop)

    def candidate_after(self, *clocks):
        for i, clock in enumerate(clocks):
            tdt.publish(self.fold, self.KEY, tdt.iso(100 + i), tdt.record('a', 'L', clock), ages={'a': 1000})
        self.mirror.feed(shadow.Projection(self.KEY, 'snapshot', 1, tdt.iso(100 + len(clocks) - 1),
                                           (tdt.record('a', 'L', clocks[-1]),), ()))
        return stc.ceiling_candidate(cv.capture('m', 'a'), 110.0)[:2]

    def test_a_seeded_turn_has_a_finite_minimum_and_no_trusted_expiry(self):
        answer = self.candidate_after((True, 5, 5))
        [estimate] = self.fold._estimates.values()
        self.assertIsNotNone(estimate.minimum, 'CONTROL: the fold holds a finite minimum')
        self.assertFalse(estimate.authoritative)
        self.assertEqual(answer, (NK, 'strict-expiry'), 'a non-authoritative minimum was trusted as an expiry')

    def test_a_turn_seen_to_begin_after_a_close_is_trusted(self):
        self.assertEqual(self.candidate_after((False, 0, 1), (True, 5, 5)), (True, None))


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
            LEGACY_ADAPTER.apply(self, agent, case.spec)  # the legacy reader, over the legacy inputs
            decision = asyncio.run(decide(agent))
            today = stc.ceiling_population(decision.receipt)
            self.assertNotEqual(today, stc.UNKNOWN_ELIGIBILITY, f'{agent}: a real receipt was unreadable')
            if decision.renewal == 'verified' or today == stc.EXCLUDED:
                self.assertFalse(today == stc.EXCLUDED and decision.holds, f'{agent}: a hold with no ceiling decision')
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

    def test_unknown_eligibility_is_counted_apart_and_never_compared(self):
        self.observe(None, frame(holder(NOW + 1)))
        unreadable = receipt(100)
        del unreadable['nowEpoch']
        self.observe(unreadable, frame(holder(NOW + 1)))
        self.observe(receipt(100), None)  # eligible, but the candidate has no basis
        self.observe({'read': 'no-row'}, None)  # a known exclusion stays excluded whatever the basis
        owner = self.owner()
        self.assertEqual(owner['unavailableByReason'], {'claim': {'eligibility-unknown': 2, 'shadow-error': 1}})
        counts = owner['counts']
        self.assertEqual((counts['evaluated'], counts['partialCompared'], counts['unavailable']), (3, 0, 3))
        self.assertEqual(counts['partialCompared'] + counts['unavailable'], counts['evaluated'])

    def test_the_real_receipt_failure_is_eligibility_unknown_beside_an_intact_control(self):
        healthy, _ = decide_busy(rows=(1,))

        class Unreadable:
            def __iter__(self):
                raise RuntimeError('receipt builder broken')
        with mock.patch.object(cg, 'TURN_COLUMNS', Unreadable()):
            broken, _ = decide_busy(rows=(1,))
        self.assertEqual((broken.holds, broken.receipt), (True, None), 'CONTROL: the seam kept the real decision')
        for decision in (healthy, broken):
            stc.observe_ceiling(decision, cv.BusyObservation(
                {}, cv.Basis(decision, frame(holder(NOW + 1)), None, 'not-needed', ('m', 'a'))), 'claim')
        counts = self.owner()['counts']
        self.assertEqual((counts['evaluated'], counts['partialCompared']), (2, 1), 'the intact row is compared')
        self.assertEqual(self.owner()['unavailableByReason'], {'claim': {'eligibility-unknown': 1}})

    def test_unknown_eligibility_records_no_today_answer(self):
        seen = []
        real = self.recorder.record_owner

        def spy(owner, variant, site, today, *args, **kwargs):
            seen.append(today)
            return real(owner, variant, site, today, *args, **kwargs)
        with mock.patch.object(self.recorder, 'record_owner', spy):
            self.observe(None, frame(holder(NOW + 1)))
        self.assertEqual(seen, [None], 'neither decision.holds nor a made-up False stands in for today')

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
