"""`launch_mode` was the one identity field stored exactly as the caller spelled it.

An agent row carries three fields that decide how the fleet treats it: `runtime`, `session_mode` and
`launch_mode`. The first two are normalised on the way in — `_normalize_runtime`,
`_normalize_session_mode`, both in `api_core/runtime.py`. The third went to the column verbatim:

    req.machineId or "", req.launchMode or "detached",

and every reader then asks, case-sensitively:

    (row["launch_mode"] or "detached") == "none"

`none` IS THE STOP MARKER. `agent_stop_resume.py` writes it as part of stopping an agent —
`SET status = 'stopped', launch_mode = 'none'` — so it means "the operator stopped this agent; do not
start it". A row holding `"None"` reads as not-stopped at every one of those sites, and the next send
cold-starts an agent that was deliberately stopped.

FOUR PYTHON READERS AND TWO IN THE BRIDGE, and the bridge pair is the sharper half: each sits ONE
LINE from a `normalizeSessionMode(...)` call on the same object, so the sibling field was normalised
and this one was not.

    dispatch-loop.mjs             a stopped RESIDENT host is not terminated
    managed-environment-sync.mjs  a disabled MANAGED agent is synced anyway

`"None"` is the obvious accident rather than a hostile input: `str(None)` in Python produces exactly
that, `comms_register` accepts `launchMode` as a free-form `z.string()`, and `models.py` types it
`Optional[str]` with no validation.

CASE ONLY, DELIBERATELY. `session_mode` has `_SESSION_MODES` and collapses anything unknown to
`resident`; launch mode has no owning set and three known values (`detached`, `managed`, `none`), one
of which — `codex-live` — appears only in tests. Inventing a vocabulary here would be a ruling.
Folding case is behaviour-preserving for every valid spelling and fixes the one that is not.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from service.api_core.runtime import _normalize_launch_mode, _normalize_session_mode

REPO = pathlib.Path(__file__).resolve().parents[2]


class NormalizeLaunchModeTests(unittest.TestCase):
    def test_a_stop_marker_is_recognised_however_it_is_spelled(self):
        """THE ONE THAT MATTERS. Each of these read as not-stopped before the fix."""
        for spelling in ("none", "None", "NONE", "  none  ", "nOnE", "\tnone\n"):
            with self.subTest(spelling=spelling):
                self.assertEqual(_normalize_launch_mode(spelling), "none")

    def test_the_other_known_modes_survive(self):
        for value, expected in (("detached", "detached"), ("Detached", "detached"),
                                ("managed", "managed"), ("MANAGED", "managed")):
            with self.subTest(value=value):
                self.assertEqual(_normalize_launch_mode(value), expected)

    def test_absence_means_detached(self):
        """The default the write path already used — `req.launchMode or "detached"`."""
        for absent in (None, "", "   ", "\t"):
            with self.subTest(absent=absent):
                self.assertEqual(_normalize_launch_mode(absent), "detached")

    def test_an_unknown_mode_is_folded_not_replaced(self):
        """Deliberately unlike `_normalize_session_mode`, which collapses the unknown to a default.

        Asserted side by side so the difference is a decision on the record rather than an
        inconsistency someone later 'fixes' in the direction that loses information.
        """
        self.assertEqual(_normalize_launch_mode("Codex-Live"), "codex-live")
        self.assertEqual(_normalize_launch_mode("future-mode"), "future-mode")
        self.assertEqual(_normalize_session_mode("future-mode"), "resident")

    def test_it_is_idempotent(self):
        for value in ("none", "detached", "managed", "codex-live"):
            with self.subTest(value=value):
                self.assertEqual(_normalize_launch_mode(_normalize_launch_mode(value)), value)


class WritePathTests(unittest.TestCase):
    """The normaliser only helps at the sites that call it, so those are asserted by name.

    A source read, and the honest form here: each is one argument inside a long INSERT tuple, so
    there is nothing to import and calling the route would need a database. It proves which helper
    each write site uses — not what the readers then do, which is the class above.
    """

    WRITE_SITES = {
        "service/api_core/agent_registration_writes.py": "_normalize_launch_mode(req.launchMode)",
        "service/api_core/resident_takeover_writes.py": '"launchMode": _normalize_launch_mode(req.launchMode)',
        "service/routers/agents/registration.py": '_normalize_launch_mode(req.launchMode) == "managed"',
    }

    def test_every_write_site_normalises(self):
        for rel, needle in sorted(self.WRITE_SITES.items()):
            with self.subTest(file=rel):
                source = (REPO / rel).read_text(encoding="utf-8")
                self.assertIn(needle, source)

    def test_no_site_still_stores_the_raw_value(self):
        """The census, so a NEW write path cannot reintroduce it.

        Scans every non-test Python file for `req.launchMode` used without the normaliser, which is
        what the three sites above looked like before this change.
        """
        offenders = []
        for path in sorted((REPO / "service").rglob("*.py")):
            rel = path.relative_to(REPO).as_posix()
            if "tests" in path.relative_to(REPO).parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if not (isinstance(node, ast.Attribute) and node.attr == "launchMode"):
                    continue
                if not (isinstance(node.value, ast.Name) and node.value.id == "req"):
                    continue
                offenders.append((rel, node.lineno))
        # Every remaining `req.launchMode` must be the ARGUMENT of the normaliser, which the
        # per-file check above already proves; here we only assert the count has not grown.
        self.assertEqual(
            len(offenders), len(self.WRITE_SITES),
            f"a new `req.launchMode` read appeared at {offenders} — it must be normalised, or the "
            f"stop marker stops being recognised there",
        )
