"""Decision 2: aify-env announces how often it reports, and comms derives "not reporting" from it (plan
docs/superpowers/plans/2026-10-21-report-interval.md). One test per row of the plan's witness table.

Contact and data stay apart: any applied, advancing, non-retired body renews the publisher's report pair, and only a
data publication makes its rows usable. Bodies go through the real route and store into a real database.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from service.tests._base import FastApiTestCase
from service.tests.test_agent_state_receiver import body, row, wire, AT
from service.tests.write_fence_race import dump

MACHINE, AGENT = ' Host ', 'alpha'
KEY = (MACHINE, 'CaseSensitive')
EVERY = 60_000


def at(seconds):
    """AT plus `seconds`, as the receipt clock writes it."""
    start = datetime.fromisoformat(AT.replace('Z', '+00:00'))
    return (start + timedelta(seconds=seconds)).astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def idle(agent=AGENT, lifetime='real-old'):
    return dict(row(agent, lifetime), state='idle', stateCause='at-prompt', busy=False, turn=None)


def announced(kind='snapshot', every=EVERY, **extra):
    value = body(kind, **extra)
    if every is not None:
        value['reportEveryMs'] = every
    return value


class ReportIntervalIsAifyEnvs(FastApiTestCase):
    def setUp(self):
        super().setUp()
        from service.api_core import operational_view, partial_status_shadow as shadow, report_age
        self.shadow, self.ov, self.age = shadow, operational_view, report_age
        for p in (patch.object(shadow, 'mirror', shadow.Mirror()), patch.object(shadow, 'recorder', shadow.Recorder()),
                  patch.object(shadow, '_quarantined', False)):
            p.start()
            self.addCleanup(p.stop)

    def post(self, value, when=AT):
        with patch('service.clock.now', return_value=when):
            return self.client.post('/api/v1/agent-state', content=value if isinstance(value, bytes) else wire(value),
                                    headers={'content-type': 'application/json'})

    def applied(self, value, when=AT):
        response = self.post(value, when)
        self.assertEqual(response.status_code, 204, response.text)

    def publisher(self):
        return self.shadow.mirror.view().get(KEY)

    def report_age(self, when):
        return self.age.report_age(self.publisher(), when)

    def holders(self, when):
        return self.ov.holders_of(self.shadow.Binding(self.shadow.mirror.view(), when), MACHINE, AGENT)

    # -- the field ---------------------------------------------------------------------------------------

    def test_an_absent_field_is_accepted_and_a_malformed_one_is_refused_with_nothing_written(self):
        self.applied(body(agents=[idle()]))  # control: today's aify-env announces nothing and still validates
        for edge in (1_000, 3_600_000):
            with self.subTest(edge=edge):
                self.assertEqual(self.post(announced(every=edge, publication=edge)).status_code, 204)
        for value in (0, -1, 1.5, '60000', True, None, 3_600_001, 999):
            for kind in ('snapshot', 'changes', 'unavailable'):
                with self.subTest(value=repr(value), kind=kind):
                    before = dump(self._db_path)
                    refused = self.post(dict(body(kind, publication=9_000_000), reportEveryMs=value))
                    self.assertEqual(refused.status_code, 422, refused.text)
                    self.assertIn('reportEveryMs', refused.text)
                    self.assertEqual(dump(self._db_path), before, 'a refused body wrote')
        retired = dict(body('retired', publication=9_000_001, reason='shutdown'), reportEveryMs=EVERY)
        retired.pop('agents', None), retired.pop('removed', None), retired.pop('complete', None)
        self.assertEqual(self.post(retired).status_code, 422, 'a retirement announces no interval')

    # -- the derived limit -------------------------------------------------------------------------------

    def test_three_announced_intervals_are_the_limit(self):
        for every, seconds, expected in ((60_000, 179.9, 'fresh'), (60_000, 180, 'stale'), (20_000, 59.9, 'fresh'),
                                         (20_000, 60, 'stale')):
            with self.subTest(every=every, seconds=seconds):
                publisher = self.shadow.Publisher(1, AT, True, False, (), report=(AT, every))
                self.assertEqual(self.age.report_age(publisher, at(seconds)), expected)

    def test_no_announcement_is_not_reporting_never_fresh(self):
        for report in (None, (AT, None)):
            publisher = self.shadow.Publisher(1, AT, True, False, (), report=report)
            for seconds in (0, 1, 179):
                with self.subTest(report=report, seconds=seconds):
                    self.assertEqual(self.age.report_age(publisher, at(seconds)), 'not-reporting')

    # -- contact without data ----------------------------------------------------------------------------

    def test_unavailable_reports_keep_contact_and_never_make_data_fresh(self):
        self.applied(announced(agents=[idle()]))
        for n in range(1, 11):  # an unavailable report every 60 s for 10 minutes
            self.applied(announced('unavailable', publication=n + 1), at(60 * n))
        self.assertEqual(self.report_age(at(601)), 'fresh', 'contact: the last report arrived 1 s ago')
        self.assertEqual(self.publisher().data_applied_at, AT, 'an unavailable report wrote the data time')
        holders = self.holders(at(601))
        self.assertEqual([h.availability for h in holders.holders], ['unavailable'], 'operationally unknown')
        self.assertEqual(self.ov.comms_view(holders, self.ov.Messaging()).eligibility, 'unknown')
        self.applied(announced(publication=12, agents=[idle()]), at(602))  # control: a complete snapshot
        self.assertEqual(self.ov.comms_view(self.holders(at(603)), self.ov.Messaging()).eligibility, 'known')

    def test_a_body_that_is_not_applied_never_renews_the_pair_and_a_retirement_removes_the_publisher(self):
        first = announced(agents=[idle()])
        self.applied(first)
        pair = self.publisher().report
        later = at(100)
        cases = {
            'duplicate': (first, 200),
            'stale': (announced(generation=9, agents=[idle()]), 200),
            'conflict': (announced(publication=1, agents=[idle('beta')]), 200),
            'refused': (dict(announced(publication=2), reportEveryMs=0), 422),
        }
        for name, (value, status) in cases.items():
            with self.subTest(name):
                response = self.post(value, later)
                self.assertEqual(response.status_code, status, response.text)
                if status == 200:  # the label is the branch the store took, read back, not the setup's intent
                    self.assertEqual(response.json(), {'applied': False, 'reason': name})
                self.assertEqual(self.publisher().report, pair, f'a {name} body renewed the pair')
        retired = dict(body('retired', publication=2, reason='shutdown'))
        for key in ('agents', 'removed', 'complete'):
            retired.pop(key, None)
        self.assertEqual(self.post(retired, later).status_code, 204)
        self.assertIsNone(self.publisher(), 'a retired publisher is gone, pair and all')

    def test_an_accepted_body_without_the_field_withdraws_the_interval(self):
        self.applied(announced(agents=[idle()]))
        self.assertEqual(self.report_age(at(1)), 'fresh', 'control: announced')
        self.applied(announced('changes', every=None, publication=2, agents=[idle()]), at(2))
        self.assertEqual(self.report_age(at(3)), 'not-reporting')

    def test_a_restored_key_recovers_contact_by_any_report_and_eligibility_only_by_its_own_snapshot(self):
        from service.api_core import mirror_restore
        from service.db import get_db
        self.applied(announced(agents=[idle()]))
        self.shadow.mirror = self.shadow.Mirror()
        self.assertEqual(asyncio.run(mirror_restore.restore_mirror(get_db)).restored, (KEY,))
        self.assertEqual(self.report_age(at(1)), 'not-reporting', 'the pair is process-local: none after a restart')
        self.assertEqual(self.ov.comms_view(self.holders(at(1)), self.ov.Messaging()).eligibility, 'unknown')
        self.applied(announced('unavailable', publication=2), at(2))
        self.assertEqual(self.report_age(at(3)), 'fresh', 'any accepted report restores contact')
        self.assertTrue(self.publisher().restored, 'an unavailable report cleared the restored guard')
        self.assertEqual(self.ov.comms_view(self.holders(at(3)), self.ov.Messaging()).eligibility, 'unknown')
        self.applied(announced(publication=3, agents=[idle()]), at(4))
        self.assertEqual(self.ov.comms_view(self.holders(at(5)), self.ov.Messaging()).eligibility, 'known')

    # -- the transition: nothing live moves before SW5 ---------------------------------------------------

    def test_lifecycle_delegation_still_delegates_to_a_fresh_publisher_that_announced_nothing(self):
        from service.api_core import lifecycle_delegation
        self.applied(body(agents=[idle()]))  # today's aify-env: no announcement
        self.assertEqual(self.publisher().report, (AT, None))
        fresh = lifecycle_delegation._fresh_publishers(MACHINE, self.shadow.mirror.view(), at(1))
        self.assertEqual(len(fresh), 1, 'the live lifecycle reader moved before SW5')

    def test_the_operational_view_reads_unknown_for_a_holder_that_announced_nothing(self):
        self.applied(body(agents=[idle()]))
        holders = self.holders(at(1))
        self.assertEqual([h.availability for h in holders.holders], ['not-reporting'])
        self.assertEqual(self.ov.comms_view(holders, self.ov.Messaging()).eligibility, 'unknown')
