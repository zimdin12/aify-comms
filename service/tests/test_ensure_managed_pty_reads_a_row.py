"""`ensure_managed_pty` reads its active terminal as a sqlite3.Row, which has no `.get()`.

Split out of test_status_starting.py in 0.9.9 S3b, when the rest of that file went with the legacy derivation and
the starting window it ranked. This class guards live code: the restart path's eager PTY.
"""

from __future__ import annotations

import unittest
from pathlib import Path


class EnsureManagedPtyRowAccessTests(unittest.TestCase):
    """`_active_terminal_for_agent` returns a sqlite3.Row, and Row has no `.get()`.

    THE BUG THIS PINS, which was mine and shipped: the restart fix compared sessions with
    `active.get("session_id")`. On a Row that raises AttributeError, and the caller's
    `except Exception: pass` swallowed it — so the eager PTY silently did nothing and the agent came
    back from a restart with no worker and no log line.

    It only fired when an active terminal EXISTED and had not yet flipped to `stopped`, which is a
    race: my first live restart test passed, the second hung. A test that only exercises the
    no-active-terminal path cannot see it, so this one constructs the Row.
    """

    def test_a_real_sqlite_row_has_no_get(self):
        """The property the production code must respect. If sqlite3.Row ever grows `.get()` this
        test tells us the constraint relaxed, rather than leaving a comment that quietly stops
        being true."""
        import sqlite3

        db = sqlite3.connect(":memory:")
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT 'sess_1' AS session_id").fetchone()
        self.assertFalse(hasattr(row, "get"), "sqlite3.Row has no .get() — index it instead")
        self.assertEqual(row["session_id"], "sess_1")

    def test_the_production_comparison_uses_indexing(self):
        """SOURCE-SCANNING because the callable path needs a live DB and a bridge; the defect was
        purely the accessor, and this is the cheapest honest guard against it returning.

        It used to be source-PINNED, which is a different and worse thing — see below. The word is
        corrected here rather than left as an inaccuracy readers have to reconcile with the paragraph
        under it, because a docstring that describes the previous implementation is how ownership prose
        drifts out of true in the first place.

        v0.5.4: this probe FOUND its subject failing rather than passing vacuously — it named
        `control_plane.py` and raised ValueError when `_ensure_managed_pty_for_dispatch` moved to
        `api_core/dispatch_start.py`. That is the correct degradation for a presence assertion and it
        is why the absence-assertion class was the dangerous one. Still, failing on every move is a
        cost with no benefit, so it now SEARCHES the service tree and requires exactly one definition
        — a second copy would let this pick one at random and guard the wrong body.
        """
        from pathlib import Path

        from service.tests._source import code_only

        needle = "async def _ensure_managed_pty_for_dispatch"
        root = Path(__file__).resolve().parents[1]
        owners = [
            path for path in sorted(root.rglob("*.py"))
            if "__pycache__" not in path.parts and "tests" not in path.parts
            and needle in code_only(path.read_text(encoding="utf-8", errors="replace"))
        ]
        self.assertEqual(
            len(owners), 1,
            f"{needle} must have exactly one definition; found {[p.name for p in owners]}",
        )
        src = code_only(owners[0].read_text(encoding="utf-8", errors="replace"))
        at = src.index(needle)
        body = src[at : at + 2500]
        self.assertIn('active["session_id"]', body)
        self.assertNotIn('active.get(', body, "Row does not support .get() — this raises")

    def test_the_eager_pty_failure_is_logged_not_swallowed(self):
        """A best-effort step that fails invisibly is indistinguishable from one with nothing to do.
        This one hid an AttributeError through two live restarts."""
        from pathlib import Path

        from service.tests._source import code_only

        # FINDS THE CODE RATHER THAN NAMING ITS FILE. This comment used to say "this probe follows
        # the code, not the file" and then named a file — which is how it went red on a v0.5.4
        # relocation that changed nothing about the logging. The warning is what matters, wherever
        # it lives, so the product tree is searched for it.
        service_dir = Path(__file__).resolve().parents[1]
        holders = [
            path for path in service_dir.rglob("*.py")
            if "tests" not in path.parts
            and "eager managed PTY for" in path.read_text(encoding="utf-8", errors="replace")
        ]
        self.assertEqual(1, len(holders), f"expected exactly one writer of this warning: {holders}")
        src = code_only(holders[0].read_text(encoding="utf-8", errors="replace"))
        at = src.index("eager managed PTY for")
        window = src[max(0, at - 400) : at + 200]
        self.assertIn("logger.warning", window)
        self.assertIn("except Exception as exc", window)
