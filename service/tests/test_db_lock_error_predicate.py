"""`_is_lock_error` — the one question that decides whether an exception is swallowed.

No test file named this module. It is four lines of text matching, and it sits under four call sites
that all do the same thing with the answer: if it says "contention", the exception disappears and
the caller serves something reassuring instead.

  * `longpoll.py` turns it into the claim's `lock_result` — `{"ok": True, "run": None}`, i.e. "there
    is nothing for you to do";
  * `routers/agents/identity.py` (twice) and `routers/sessions.py` re-raise everything it rejects
    and swallow everything it accepts, serving cached data rather than a 503.

SO IT FAILS IN TWO DIRECTIONS AND NEITHER RAISES.

TOO NARROW is the failure this repo already lived through: a transient `database is locked` that is
not recognised becomes a 503 on a read endpoint, which is the symptom the whole in-memory status
cache was introduced to end. Every real message form is pinned below for that reason.

TOO BROAD is the quieter one: an unrelated exception classified as contention is not reported
anywhere. A claim that failed for a real reason returns "nothing to do" and the bridge waits; a read
endpoint serves stale data and looks healthy. There is no log line and no status.

WHICH IS WHY `"blocked"` IS TESTED. It contains `locked` as a substring — `"blocked"[1:]` is exactly
`"locked"` — so before 2026-08-17 any exception whose text mentioned a block was classified as SQLite
contention and swallowed. Nothing on these paths raises such a message TODAY (measured, not assumed:
no `HTTPException` in the claim or read paths carries `blocked` or `busy` text), so this was latent
rather than live — but `blockedBy` is live vocabulary in this service's dispatch layer, and the two
words were one refusal away from meeting.
"""

from __future__ import annotations

import sqlite3
import unittest

from service.db_errors import _is_lock_error


class RealContentionTests(unittest.TestCase):
    """Everything that must keep being recognised. Narrowing this brings the 503s back."""

    def test_the_message_python_sqlite3_actually_raises(self):
        self.assertTrue(_is_lock_error(sqlite3.OperationalError("database is locked")))

    def test_the_table_level_variant(self):
        self.assertTrue(_is_lock_error(sqlite3.OperationalError("database table is locked")))

    def test_a_busy_variant(self):
        self.assertTrue(_is_lock_error(sqlite3.OperationalError("database is busy")))

    def test_the_SQLITE_BUSY_result_code_spelling(self):
        """Wrappers and drivers surface the result code rather than the sentence. The underscore is
        why the left-hand guard added in 2026-08-17 is a lookbehind on LETTERS and not `\\b` — a word
        boundary would not fire after `_`, and this form would have stopped matching."""
        self.assertTrue(_is_lock_error(Exception("SQLITE_BUSY: database is locked")))
        self.assertTrue(_is_lock_error(Exception("sqlite_busy")))

    def test_the_check_is_CASE_INSENSITIVE(self):
        self.assertTrue(_is_lock_error(Exception("DATABASE IS LOCKED")))

    def test_a_wrapped_message_still_matches(self):
        """aiosqlite re-raises through its own executor, and the callers see whatever text arrives —
        so the match is on the text, not on the exception CLASS."""
        self.assertTrue(_is_lock_error(RuntimeError("Error executing query: database is locked")))

    def test_the_exception_TYPE_is_not_consulted(self):
        """Deliberate: this module imports nothing, not even sqlite3, which is what lets it sit
        below every other module. A type check would couple it to whichever driver is in use."""
        class Odd(BaseException):
            def __str__(self):
                return "database is locked"

        self.assertTrue(_is_lock_error(Odd()))


class NotContentionTests(unittest.TestCase):
    """Everything that must be re-raised. Broadening this swallows real failures silently."""

    def test_an_ordinary_error_is_not_contention(self):
        self.assertFalse(_is_lock_error(RuntimeError("no such column: agents.favourite")))

    def test_a_readonly_database_is_NOT_contention(self):
        """A real SQLite error and a real outage — the data directory is mounted read-only, or the
        file lost its permissions. Retrying will never fix it, and classifying it as contention
        turns a broken deployment into an endpoint that quietly serves cached data."""
        self.assertFalse(_is_lock_error(sqlite3.OperationalError(
            "attempt to write a readonly database")))

    def test_a_missing_table_is_NOT_contention(self):
        self.assertFalse(_is_lock_error(sqlite3.OperationalError("no such table: agents")))

    def test_an_INTEGRITY_error_is_not_contention(self):
        self.assertFalse(_is_lock_error(sqlite3.IntegrityError("UNIQUE constraint failed")))

    def test_an_exception_with_no_message_is_not_contention(self):
        self.assertFalse(_is_lock_error(RuntimeError()))

    def test_None_is_not_contention(self):
        """The callers pass whatever `except Exception as exc` bound. Answering True for nothing at
        all would swallow on a path that never had an error to classify."""
        self.assertFalse(_is_lock_error(None))


class NearMissTests(unittest.TestCase):
    """Words that CONTAIN the markers. This is where the predicate was over-broad."""

    def test_BLOCKED_is_not_LOCKED(self):
        """`"blocked"[1:]` is `"locked"`. Until 2026-08-17 every one of these was classified as
        SQLite contention and swallowed — including, on the claim path, a refusal in a service whose
        dispatch layer answers with `blockedBy`."""
        for message in (
            "recipient is blocked",
            "blocked by an active run",
            "PermissionError: access blocked by policy",
            "blockedBy: other-agent",
        ):
            with self.subTest(message=message):
                self.assertFalse(_is_lock_error(RuntimeError(message)))

    def test_UNLOCKED_is_not_LOCKED(self):
        self.assertFalse(_is_lock_error(RuntimeError("the keyring is unlocked")))

    def test_a_word_that_merely_STARTS_with_busy_still_matches(self):
        """The guard is on the LEFT side only, and deliberately: the false positive that actually
        exists is a marker with a prefix, and every real form ends the word there or continues into
        punctuation. Erring broad on the right keeps `busy_timeout`-style texts matching."""
        self.assertTrue(_is_lock_error(RuntimeError("busy_timeout exceeded")))


class CallerContractTests(unittest.TestCase):
    """The predicate only matters through its callers, and all four use it the same way."""

    def test_a_lock_error_becomes_the_long_polls_EMPTY_result(self):
        """`longpoll(lock_result=...)`: contention means "nothing claimed this round", not a 503.
        The attempt's own connection is already closed in its `finally`, so nothing leaks."""
        import asyncio

        from service import longpoll

        async def attempt():
            raise sqlite3.OperationalError("database is locked")

        result = asyncio.run(longpoll.longpoll(
            0, attempt, lambda r: r.get("run") is None,
            lock_result={"ok": True, "run": None},
        ))
        self.assertEqual(result, {"ok": True, "run": None})

    def test_a_NON_lock_error_still_propagates_through_the_long_poll(self):
        """The half that matters more. If this were swallowed too, every failure inside a claim
        would read to the bridge as "no work available"."""
        import asyncio

        from service import longpoll

        async def attempt():
            raise sqlite3.OperationalError("no such table: dispatch_runs")

        with self.assertRaises(sqlite3.OperationalError):
            asyncio.run(longpoll.longpoll(
                0, attempt, lambda r: r.get("run") is None,
                lock_result={"ok": True, "run": None},
            ))

    def test_without_a_lock_result_even_contention_propagates(self):
        """`lock_result=None` is the opt-out, and it has to stay one: a caller that has not decided
        what "empty" looks like must not have an exception hidden from it."""
        import asyncio

        from service import longpoll

        async def attempt():
            raise sqlite3.OperationalError("database is locked")

        with self.assertRaises(sqlite3.OperationalError):
            asyncio.run(longpoll.longpoll(0, attempt, lambda r: r.get("run") is None))


if __name__ == "__main__":
    unittest.main()
