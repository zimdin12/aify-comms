"""`clock.iso_to_epoch` reads 0.0 for a time it cannot parse, unless the caller asks for `None`.

The analytics span loop asks for `None`: a run whose start time is garbage must be skipped, and 0.0
would count it as working since 1970. It carried its own nested parser for that until 0.7.0.
"""

import unittest

from service.clock import iso_to_epoch


class IsoToEpochCanSayUnknownTests(unittest.TestCase):
    def test_an_unreadable_time_is_the_default_the_caller_chose(self):
        for value in ("", None, "not a time"):
            with self.subTest(value=value):
                self.assertEqual(iso_to_epoch(value), 0.0)
                self.assertIsNone(iso_to_epoch(value, default=None))

    def test_control_a_real_time_ignores_the_default(self):
        self.assertEqual(iso_to_epoch("1970-01-01T00:01:00Z", default=None), 60.0)
