"""An environment running code its own disk has moved past, and the case that must not read as a pass.

`bridge-current` answered a version of this until v0.6.1 retired the tier that reported a build of this
repo. The question outlived the check: ten commits sitting inert until a restart is exactly the signal
that matters, and the operator will not run a command to find out. So the tier advertises what it
LOADED beside what is on DISK and this compares them.

THE STATE THIS FILE IS REALLY ABOUT IS `unknown`. An advertiser too old to send `codeOnDisk` gathers no
evidence, and this project has shipped "no evidence" as a pass twice -- `env-bridge` reporting "2
connected" with zero bridges alive, and `bridge-current` green-by-default whenever nothing reported.
Both were checks that could not answer and said so as ok.
"""

import unittest

from service.api_core.code_currency import CURRENT, STALE, UNKNOWN, code_currency


class CodeCurrencyTests(unittest.TestCase):
    def test_matching_identities_are_current(self):
        verdict = code_currency({"instance": "aaaa1111", "codeOnDisk": "aaaa1111"})
        self.assertEqual(verdict["state"], CURRENT)

    def test_differing_identities_are_stale_and_BOTH_travel(self):
        # Both, because the remedy is a restart and restarting an environment tier reaps the managed
        # workers it is running. Advice with that price on it has to be arguable.
        verdict = code_currency({"instance": "aaaa1111", "codeOnDisk": "bbbb2222"})
        self.assertEqual(verdict["state"], STALE)
        self.assertEqual(verdict["running"], "aaaa1111")
        self.assertEqual(verdict["onDisk"], "bbbb2222")

    def test_a_missing_half_is_UNKNOWN_and_never_current(self):
        # The trap. An advertiser too old to report the disk build sends nothing, and reading that as
        # `current` would answer "did my restart take?" with "yes" on the evidence of nothing. It is
        # also the ordinary state DURING an upgrade, which is exactly when somebody reads this.
        for metadata in (
            {"instance": "aaaa1111"},
            {"codeOnDisk": "aaaa1111"},
            {"instance": "aaaa1111", "codeOnDisk": ""},
            {"instance": "", "codeOnDisk": "bbbb2222"},
            {},
        ):
            with self.subTest(metadata=metadata):
                self.assertEqual(code_currency(metadata)["state"], UNKNOWN)

    def test_a_missing_half_is_NOT_stale_either(self):
        # The opposite error, and the more expensive one: reporting stale would send an operator to
        # restart a daemon that is fine, and the restart costs them every worker it was running.
        self.assertNotEqual(code_currency({"instance": "aaaa1111"})["state"], STALE)

    def test_it_still_reports_the_half_it_did_get(self):
        # "unknown" is more useful when it can say which of the two arrived.
        self.assertEqual(code_currency({"instance": "aaaa1111"})["running"], "aaaa1111")

    def test_a_non_mapping_does_not_raise(self):
        # This runs inside row serialisation for every environment on every poll. A corrupt metadata
        # blob must degrade to "cannot tell" rather than take the listing down.
        for metadata in (None, "", [], 7):
            with self.subTest(metadata=metadata):
                self.assertEqual(code_currency(metadata)["state"], UNKNOWN)

    def test_the_verdict_is_never_a_boolean(self):
        # A two-valued field is how the third state gets collapsed back into a pass by the next person
        # writing a summary line -- and here the third state is the common one mid-upgrade.
        verdict = code_currency({"instance": "a", "codeOnDisk": "b"})
        self.assertIsInstance(verdict["state"], str)
        self.assertEqual({CURRENT, STALE, UNKNOWN}, {"current", "stale", "unknown"})


class TheRowCarriesItTests(unittest.TestCase):
    """A verdict nothing serialises changes nothing -- both ends of a new field."""

    def test_the_environment_record_carries_codeCurrency(self):
        import re
        import sqlite3

        from service.api_core.records import _environment_record_to_dict
        from service import schema

        # THE REAL DDL, not a hand-typed copy. My first version listed the columns by hand, missed
        # `registered_at`, and failed for a reason that had nothing to do with the subject -- and a
        # copy that had happened to be complete would simply rot instead.
        ddl = re.search(
            r"CREATE TABLE IF NOT EXISTS environments \((?:[^;])*\);",
            schema.SCHEMA,
        )
        self.assertIsNotNone(ddl, "the environments table is no longer declared in schema.SCHEMA")
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(ddl.group(0))
        connection.execute(
            "INSERT INTO environments (id, metadata, registered_at, last_seen) VALUES (?, ?, ?, ?)",
            (
                "env-1",
                '{"instance": "aaaa1111", "codeOnDisk": "bbbb2222"}',
                "2026-09-04T00:00:00Z",
                "2026-09-04T00:00:00Z",
            ),
        )
        row = connection.execute("SELECT * FROM environments").fetchone()
        record = _environment_record_to_dict(row)
        self.assertEqual(record["codeCurrency"]["state"], STALE)
        self.assertEqual(record["codeCurrency"]["running"], "aaaa1111")
        self.assertEqual(record["codeCurrency"]["onDisk"], "bbbb2222")


if __name__ == "__main__":
    unittest.main()
