"""`EXTERNAL_KEYS` parsing: a bad entry grants nothing, is reported by name, and never stops the service.

Pure; see service/api_core/external_keys.py. The end-to-end behaviour (what a key opens, what it may
not do) is proven against the real service in
service/tests/e2e/test_another_machine_sends_with_its_own_key.py.
"""

from __future__ import annotations

import unittest

from service.api_core.external_keys import parse_external_keys

GOOD = "a" * 32
OTHER = "b" * 32


class ExternalKeysAreReadStrictly(unittest.TestCase):
    def test_well_formed_entries_are_accepted_and_matched_to_their_machine(self) -> None:
        ring = parse_external_keys(f" pc2:{GOOD} , laptop.home:{OTHER} ")
        self.assertEqual(ring.rejected, ())
        self.assertEqual(ring.match(GOOD), "pc2")
        self.assertEqual(ring.match(OTHER), "laptop.home")
        self.assertEqual(ring.match("c" * 32), "")
        self.assertEqual(ring.match(""), "")

    def test_nothing_configured_is_an_empty_keyring(self) -> None:
        ring = parse_external_keys("")
        self.assertEqual((ring.keys, ring.rejected), ({}, ()))

    def test_each_bad_entry_is_refused_and_the_good_one_survives(self) -> None:
        cases = {
            "no separator": f"pc3{GOOD}",
            "a name that could start a line": f"pc\n3:{GOOD}",
            "a short key": "pc3:short",
            "the service key reused": f"pc3:{'s' * 32}",
        }
        for why, entry in cases.items():
            with self.subTest(why):
                ring = parse_external_keys(f"pc2:{GOOD},{entry}", reserved=("s" * 32,))
                self.assertEqual(list(ring.keys), ["pc2"], "the good entry must survive a bad neighbour")
                self.assertEqual(len(ring.rejected), 1)

    def test_a_key_shared_by_two_machines_proves_neither(self) -> None:
        ring = parse_external_keys(f"pc2:{GOOD},pc3:{GOOD}")
        self.assertEqual(ring.keys, {}, "a key two machines hold cannot say which one sent")
        self.assertEqual(ring.match(GOOD), "")

    def test_a_name_used_twice_keeps_the_first(self) -> None:
        ring = parse_external_keys(f"pc2:{GOOD},PC2:{OTHER}")
        self.assertEqual(ring.keys, {"pc2": GOOD})
        self.assertEqual(len(ring.rejected), 1)

    def test_a_refusal_never_repeats_the_key(self) -> None:
        secret = "k" * 12  # too short, so refused -- and the refusal is logged
        ring = parse_external_keys(f"pc9:{secret}")
        self.assertTrue(ring.rejected)
        self.assertFalse(any(secret in reason for reason in ring.rejected), ring.rejected)

    def test_health_shows_counts_not_machine_names(self) -> None:
        summary = parse_external_keys(f"pc2:{GOOD},bad").summary(enforced=True)
        self.assertEqual(summary, {"configured": 1, "rejected": 1, "enforced": True})


if __name__ == "__main__":
    unittest.main()
