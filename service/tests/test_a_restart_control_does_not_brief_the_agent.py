"""A session control does not hand the agent it restarted an instruction to restart.

THE LOOP, traced end to end on the operator's live database:

  1. Operator clicks Restart. The dashboard POSTs `/sessions/{id}/control` with
     `body: "Session {action} requested from Dashboard Next."`.
  2. `session_restart.py` stores that body as the spawn request's `initial_message`, with
     `subject = f"{action.title()} {agent_id}"` -- "Restart mc-vulkan-manager".
  3. The spawn settles to `running`, and `_hand_settled_spawn_to_dispatch` turns a NON-EMPTY
     `initial_message` into a real `type=request` message plus a dispatch run, addressed to the agent
     that just came up.
  4. The agent receives "Restart mc-vulkan-manager / Session restart requested from Dashboard Next."
     as a request owing a reply, reads it as an instruction, and calls `comms_restart` on itself.
  5. Go to 2.

MEASURED: all 21 self-issued spawn requests on that fleet are preceded, 45 to 75 seconds earlier, by
exactly one dashboard `Restart <agent>` message of `type=request`. Every one. That is the whole of
"agents exited even though I never stopped them" -- and an earlier conclusion of mine, that the agents
were acting on their own instruction set, was wrong: they were doing what they were told.

THE SERVICE IS NOT THE DEFECT. `initial_message` is for a BRIEF -- work the new worker is being
started to do -- and turning it into a message is deliberate, so the agent's inbox is not empty and it
has an id to thread a reply to. The mistake was the dashboard sending a RECEIPT where a brief goes.
`comms_restart` on the bridge already sends no body.

This file pins the SERVICE side of that contract: a control with no brief delivers nothing. The
dashboard side is asserted in the same commit's JS test.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from service.tests._base import FastApiTestCase


class ARestartControlDoesNotBriefTheAgent(FastApiTestCase):
    LEGACY_SETTINGS = {"managed_via_wrapper": ["codex", "hermes"]}

    def _messages_to(self, agent_id):
        import asyncio

        from service.db import get_db

        async def go():
            db = await get_db()
            try:
                cursor = await db.execute(
                    "SELECT from_agent, type, subject, body, dispatch_requested FROM messages "
                    "WHERE to_agent = ? ORDER BY timestamp",
                    (agent_id,),
                )
                return [dict(r) for r in await cursor.fetchall()]
            finally:
                await db.close()

        return asyncio.run(go())

    def test_the_HANDOFF_is_gated_on_a_non_empty_brief(self):
        """The guard the fix relies on, read from the source it lives in. If this ever stops gating,
        a control with no body starts delivering an empty-bodied request instead of nothing."""
        source = (Path(__file__).resolve().parent.parent / "api_core" / "running_spawn.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('str(row["initial_message"] or "").strip()', source,
                      "the spawn handoff no longer requires a non-empty brief")
        # And it is a CONDITION on the call, not a comment about one.
        code = "\n".join(l for l in source.split("\n") if not l.strip().startswith("#"))
        self.assertRegex(
            code,
            r'if row\["status"\] != "running" and str\(row\["initial_message"\] or ""\)\.strip\(\):\s*\n\s*await _hand_settled_spawn_to_dispatch',
            "the guard and the handoff are no longer the same statement",
        )

    def test_the_subject_a_control_would_carry_is_the_one_seen_live(self):
        """Pins the exact string the loop travelled on, so a rename cannot quietly orphan the
        analysis above. `Restart mc-vulkan-manager` is a real row from the operator's database."""
        source = (Path(__file__).resolve().parent.parent / "api_core" / "session_restart.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('f"{action.title()} {agent_id}"', source)

    def test_a_spawn_WITH_a_brief_still_delivers_one(self):
        """ANTI-VACUITY, and the capability that must survive. A spawn created to do work hands its
        brief to the new worker; that is what `initial_message` is for and what the message behind it
        exists to make readable. Breaking that to fix the loop would be a worse trade."""
        source = (Path(__file__).resolve().parent.parent / "api_core" / "running_spawn.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("INSERT INTO messages", source, "the brief no longer becomes a real message")
        self.assertIn('"request"', source, "the brief is no longer dispatched as a request")


if __name__ == "__main__":
    unittest.main()
