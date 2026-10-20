"""SW4: the D8 rows of the switched view, in the plan's precedence order (hard-switch plan, page 1)."""
from __future__ import annotations

import itertools
import unittest

from service.api_core.d8_rows import D8_OFFLINE, D8_STOPPED, D8_UNKNOWN, d8_word


def word(*, defined=True, operator_stopped=False, has_record=False, wake_handle_absent=False):
    return d8_word(defined=defined, operator_stopped=operator_stopped, has_record=has_record,
                   wake_handle_absent=wake_handle_absent)


class TheD8RowsKeepTodaysRule(unittest.TestCase):

    def test_row_1_an_operator_stop_on_an_undefined_agent_reads_stopped_even_beside_a_record(self):
        self.assertEqual(word(defined=False, operator_stopped=True, has_record=True), D8_STOPPED)
        self.assertEqual(word(defined=False, operator_stopped=True, has_record=False), D8_STOPPED)

    def test_a_defined_agents_stop_is_aify_envs_and_reaches_comms_in_its_record(self):
        self.assertIsNone(word(defined=True, operator_stopped=True, has_record=True))
        self.assertEqual(word(defined=True, operator_stopped=True, has_record=False), D8_UNKNOWN)

    def test_row_2_a_record_decides(self):
        self.assertIsNone(word(has_record=True))
        self.assertIsNone(word(defined=False, has_record=True))

    def test_row_3_no_record_reads_unknown_and_a_proven_absent_wake_handle_reads_offline(self):
        self.assertEqual(word(has_record=False), D8_UNKNOWN)
        self.assertEqual(word(defined=False, has_record=False), D8_UNKNOWN)
        self.assertEqual(word(has_record=False, wake_handle_absent=True), D8_OFFLINE)

    def test_no_row_makes_any_agent_available(self):
        """Over every input, the rows give only stopped, unknown or offline, or defer to the record."""
        for defined, stopped, record, absent in itertools.product((False, True), repeat=4):
            with self.subTest(f"defined={defined} stopped={stopped} record={record} absent={absent}"):
                self.assertIn(word(defined=defined, operator_stopped=stopped, has_record=record,
                                   wake_handle_absent=absent), {D8_STOPPED, D8_UNKNOWN, D8_OFFLINE, None})


if __name__ == "__main__":
    unittest.main()
