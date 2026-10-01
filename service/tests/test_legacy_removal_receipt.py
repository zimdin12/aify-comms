"""What a pre-column done removal migrates to (`service/legacy_removal_receipt.py`), read from the forms
12766276 actually wrote: its note, repeated on every repeated report, after whatever the host said."""
from __future__ import annotations

import unittest

from service.legacy_removal_receipt import legacy_removal_consequence

NOTE = "[service: the definition is now win32:host-b store t1 lifetime 2, not the one this removal was for; nothing removed]"


class ALegacyReceiptMigratesToWhatItRecorded(unittest.TestCase):
    def test_the_first_trailing_note_is_the_refusal_made(self):
        later = "[service: the definition this removal was for ended another way; nothing removed]"
        self.assertEqual(legacy_removal_consequence(f"host done {NOTE} {later}", True, False),
                         "nothing removed: the definition is now win32:host-b store t1 lifetime 2, "
                         "not the one this removal was for")

    def test_a_note_the_host_wrote_mid_text_is_not_the_services(self):
        self.assertEqual(legacy_removal_consequence(f"{NOTE} then the host carried on", True, False), "pending")

    def test_no_note_is_removed_or_owed_by_what_is_left(self):
        self.assertEqual(legacy_removal_consequence("", False, True), "removed")
        self.assertEqual(legacy_removal_consequence("", True, False), "pending")
        self.assertEqual(legacy_removal_consequence("", False, False), "pending")
