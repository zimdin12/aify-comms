"""`renderedCols` at a wide viewport separates the live-screen branch from the replay one.

WHY THIS EXISTS. `scripts/measure-live-console-fetch.py` reports which branch each live console
takes, and it answered LIVE for all nine on this fleet. A verdict with one observed value is not a
verdict: against the live service the instrument never once said REPLAY, because five ended consoles
still hold screens and the rest have no stored tail to render at all. Nothing there exercised the
path the answer is supposed to be distinguishable from.

THE SIGNAL, from `_attach_terminal_snapshot`'s own two arms:

  LIVE   `render_live_screen` returns the SCREEN's own cols, whatever the viewer asked for.
  REPLAY the tail is rendered at `max(source width, viewer width)`.

So a viewer WIDER than the stored geometry separates them: the replay arm widens to the viewer and
the live arm does not. It is a pure read -- the GET handler performs no write, which is what makes
it safe to point at a console somebody is watching.

THIS TEST IS THE CONTROL FOR THAT INSTRUMENT: two terminals with the SAME stored tail, one given a
live screen and one not, both asked at 200 columns. If the two ever stop separating, the script's
"every live console is on the LIVE branch" becomes a sentence with no evidence behind it, and this
goes red rather than the script going quietly meaningless.
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
import uuid
from pathlib import Path

WIDE = 200
COLS, ROWS = 132, 26


def _painted(chars: int) -> str:
    """A tail with real escape sequences, so the live screen is eligible to exist at all.

    `feed_live_screen` refuses to create a screen for a chunk containing no ESC -- "plain logs must
    remain byte-for-byte logs" -- so a plain-text fixture would put BOTH arms on the replay path and
    the test would pass by never constructing the contrast it claims to measure.
    """
    esc = chr(27)
    parts, size, row = [], 0, 1
    while size < chars:
        line = f"{esc}[{row};1H{esc}[38;5;{(row % 200) + 16}mrow {row} of a redraw{esc}[0m"
        parts.append(line)
        size += len(line)
        row = row % ROWS + 1
    return "".join(parts)


class TheConsoleBranchDiscriminatorSeparatesBothBranches(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        os.environ["AIFY_DB_PATH"] = str(
            Path(tempfile.mkdtemp(prefix="aify-branch-")) / "probe.db")
        from service.db import get_db, init_db

        await init_db(Path(os.environ["AIFY_DB_PATH"]))
        from service.api_core.terminal_output import _trim_terminal_output as trim

        self.tail = trim(_painted(8 * 1024))
        self.replay_id = f"replay-{uuid.uuid4().hex[:8]}"
        self.live_id = f"live-{uuid.uuid4().hex[:8]}"
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ")

        db = await get_db()
        await db.execute("PRAGMA foreign_keys = OFF")
        await db.execute(
            "INSERT INTO environments (id, registered_at, last_seen) VALUES (?, ?, ?)",
            ("branch-env", now, now))
        for tid in (self.replay_id, self.live_id):
            await db.execute(
                "INSERT INTO agent_sessions (id, agent_id, environment_id, runtime, started_at, "
                "last_seen) VALUES (?, ?, ?, ?, ?, ?)",
                (f"sess-{tid}", f"agent-{tid}", "branch-env", "probe", now, now))
            await db.execute(
                "INSERT INTO terminal_sessions (id, session_id, agent_id, environment_id, runtime, "
                "output, status, output_seq, created_at, updated_at, cols, rows) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tid, f"sess-{tid}", f"agent-{tid}", "branch-env", "probe", self.tail, "running",
                 1, now, now, COLS, ROWS))
        await db.commit()
        await db.close()

        # ONLY the live arm gets a screen. That is the entire difference between the two rows, and
        # it is why the same tail is used for both: a different tail would let a size effect stand
        # in for the branch.
        from service.terminal_snapshot import feed_live_screen

        self.fed = feed_live_screen(self.live_id, self.tail, cols=COLS, rows=ROWS, seq=4242)

    async def asyncTearDown(self) -> None:
        from service.terminal_snapshot import drop_live_screen

        drop_live_screen(self.live_id)
        drop_live_screen(self.replay_id)

    async def _rendered_cols(self, terminal_id: str):
        import httpx

        from service.main import app

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://127.0.0.1:8800") as client:
            response = await client.get(
                f"/api/v1/terminals/{terminal_id}?cols={WIDE}&rows={ROWS}&view=console")
            self.assertEqual(response.status_code, 200)
            return (response.json().get("terminal") or {}).get("renderedCols")

    async def test_the_fixture_actually_built_a_live_screen(self) -> None:
        """The setup control. `feed_live_screen` refuses a chunk with no ESC, and a fixture whose
        live arm never got a screen would put BOTH arms on the replay path -- where they agree, and
        the test below would pass having measured nothing."""
        self.assertTrue(self.fed, "the live arm was never given a screen, so there is no contrast")

    async def test_a_terminal_with_no_live_screen_renders_at_the_viewer_width(self) -> None:
        """REPLAY: the tail is rendered at max(source, viewer), so a wide viewer widens it."""
        self.assertEqual(
            await self._rendered_cols(self.replay_id), WIDE,
            "a terminal with no live screen did not render at the viewer's width, so the "
            "discriminator cannot report REPLAY and its LIVE verdicts mean nothing")

    async def test_a_terminal_with_a_live_screen_keeps_the_screen_width(self) -> None:
        """LIVE: `render_live_screen` returns the screen's own geometry, ignoring the viewer."""
        self.assertEqual(
            await self._rendered_cols(self.live_id), COLS,
            "a terminal WITH a live screen widened to the viewer, so the two branches are no "
            "longer separable by this signal")

    async def test_the_two_branches_disagree(self) -> None:
        """The relation, not either value on its own: the whole point is that they DIFFER.

        Asserting each width separately would still pass if some future change made both arms
        return the same number by coincidence of the constants chosen here.
        """
        replay = await self._rendered_cols(self.replay_id)
        live = await self._rendered_cols(self.live_id)
        self.assertNotEqual(
            replay, live,
            f"both branches answered {replay!r} at a {WIDE}-column viewer, so this signal "
            f"separates nothing and the script that relies on it is reporting a coin flip")


if __name__ == "__main__":
    unittest.main()
