"""A terminal row carries ARGV beside its command string, and the two cannot disagree.

v0.6 Phase 8. aify-comms composes a shell command STRING; aify-env executes an allowlisted launcher
FILE plus arguments. Bridging those needs the structural form to reach the bridge, and today it does
not: `console_argv` exists on every adapter but is joined before anything stores it.

ADDITIVE, DELIBERATELY. `command` stays exactly as it is, so a bridge that has not been updated reads
what it always read and every terminal row already queued keeps working. `argv` sits beside it for a
bridge that knows to prefer it.

THE AGREEMENT IS THE SAFETY PROPERTY. If the two ever described different launches, a delegated spawn
and a legacy one would start different processes from the same row -- the "subtly different in ways
nobody could attribute" failure the seam refuses to risk. So the string is DERIVED from the argv rather
than stored alongside it, and these tests pin that.
"""

from __future__ import annotations

import unittest

from service.api_core.capabilities import _default_console_argv, _default_console_command
from service.runtimes import _REGISTRY  # noqa: PLC2701 - the registry IS the population under test


def _session(runtime: str, handle: str = "") -> dict:
    return {"agent_id": "agent-1", "session_handle": handle, "runtime": runtime, "id": "s1"}


class TerminalArgvAccompaniesCommand(unittest.TestCase):
    def test_every_runtime_produces_argv_that_joins_to_its_command(self):
        for runtime in sorted(_REGISTRY):
            for handle in ("", "sess-abc"):
                for interactive in (False, True):
                    with self.subTest(runtime=runtime, handle=handle, interactive=interactive):
                        session = _session(runtime, handle)
                        argv = _default_console_argv(session, "/w", interactive=interactive)
                        command = _default_console_command(session, "/w", interactive=interactive)
                        self.assertIsInstance(argv, list)
                        self.assertTrue(argv, "argv is empty, so there is no program to run")
                        self.assertEqual(" ".join(argv), command)

    def test_argv_holds_no_element_containing_a_space(self):
        """While this holds, the string and the list carry the same information.

        A joined string cannot express an argument with a space in it, so the day one appears the two
        forms stop being interchangeable -- and that is the day the structural form stops being
        optional. This fails then, which is when someone needs to know.
        """
        for runtime in sorted(_REGISTRY):
            for part in _default_console_argv(_session(runtime, "h"), "/w"):
                with self.subTest(runtime=runtime, part=part):
                    self.assertNotIn(" ", part)

    def test_an_unknown_runtime_still_yields_a_runnable_argv(self):
        """The command builder has a fallback for a runtime with no adapter; argv must have one too,
        or a row that previously produced a command string would produce an empty list."""
        session = _session("no-such-runtime")
        argv = _default_console_argv(session, "/w")
        self.assertTrue(argv)
        self.assertEqual(" ".join(argv), _default_console_command(session, "/w"))


if __name__ == "__main__":
    unittest.main()


class TerminalRecordExposesArgv(unittest.TestCase):
    """The API has to carry argv, or storing it changes nothing a bridge can see."""

    @staticmethod
    def _row(**over):
        base = {
            "id": "t1", "session_id": "s1", "agent_id": "a1", "environment_id": "e1",
            "bridge_id": "", "runtime": "claude-code", "workspace": "/w",
            "command": "claude-aify --aify-agent a1", "argv": '["claude-aify","--aify-agent","a1"]',
            "output": "", "output_seq": 0, "cols": 0, "rows": 0, "status": "starting",
            "requested_by": "dashboard", "created_at": "t", "updated_at": "t",
            "stopped_at": None, "error": "", "process_id": "",
        }
        base.update(over)
        return _FakeRow(base)

    def test_argv_is_returned_as_a_list(self):
        from service.api_core.records import _terminal_session_to_dict
        d = _terminal_session_to_dict(self._row())
        self.assertEqual(d["argv"], ["claude-aify", "--aify-agent", "a1"])
        self.assertEqual(" ".join(d["argv"]), d["command"])

    def test_a_row_without_the_column_yields_an_empty_list(self):
        """A database that predates the migration must still serve terminals."""
        from service.api_core.records import _terminal_session_to_dict
        row = self._row()
        del row.data["argv"]
        self.assertEqual(_terminal_session_to_dict(row)["argv"], [])

    def test_malformed_argv_yields_an_empty_list_rather_than_breaking_the_fetch(self):
        """Never fail a terminal fetch over an advisory field -- the same rule the role field follows.

        An empty argv means "no structural form here", which every consumer must already handle
        because an operator-supplied command legitimately has none.
        """
        from service.api_core.records import _terminal_session_to_dict
        for bad in ("{not json", '"a string"', "42", "null", ""):
            with self.subTest(bad=bad):
                self.assertEqual(_terminal_session_to_dict(self._row(argv=bad))["argv"], [])


class _FakeRow:
    """sqlite3.Row-alike: subscript access plus keys()."""

    def __init__(self, data):
        self.data = data

    def __getitem__(self, key):
        return self.data[key]

    def keys(self):
        return list(self.data.keys())
