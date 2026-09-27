"""What compaction decides: when a runtime's own command may be typed, and what a handoff brief says.

`service/api_core/compaction.py` is pure, so every refusal is exercised here directly, one input
changed at a time. The routes that gather those inputs are driven in
`test_compaction_routes_type_the_command_and_serve_the_brief.py`.

WHY EACH REFUSAL EXISTS, because each is a keystroke that would otherwise land somewhere wrong:
  * resident      -- the console is the operator's own terminal; not aify-comms' to type into.
  * no command    -- pi and opencode have no verified one, and a guess is typed into a live TUI.
  * no console    -- nothing is running to compact.
  * virtual       -- a bridge-synthesized console has no TUI; the command would arrive as a prompt.
  * working       -- typed mid-turn it is queued behind the turn or read as input to it.
  * blocked       -- a dialog is on screen, and the command plus Enter would ANSWER it.
"""

from __future__ import annotations

import unittest

from service.api_core import compaction as c
from service.runtimes import _REGISTRY, adapter_for

AT_REST = dict(session_mode="managed", runtime="claude-code", status="online", terminal_id="term_1",
               terminal_command="claude-aify --aify-agent a")


def decide(**overrides):
    return c.decide_native_compact(c.NativeCompactTarget(agent_id="a", **{**AT_REST, **overrides}))


class NativeCompactDecisionTests(unittest.TestCase):
    def test_an_idle_managed_worker_gets_its_runtimes_own_command(self):
        """The allowed case per runtime, and the one place each command is asserted. Both statuses
        that mean "at its prompt" pass: `shell` is an idle prompt with background shells running."""
        for runtime, command in (("claude-code", "/compact"), ("codex", "/compact"), ("hermes", "/compress")):
            for status in ("online", "shell"):
                with self.subTest(runtime=runtime, status=status):
                    decision = decide(runtime=runtime, status=status)
                    self.assertTrue(decision.allowed, decision.message)
                    self.assertEqual(decision.command, command)
                    self.assertEqual(decision.refused, "")

    def test_each_refusal_fires_alone_and_types_nothing(self):
        """One input moved off the allowed case at a time, so no guard is covered by another."""
        cases = [
            ({"session_mode": "resident"}, c.RESIDENT),
            ({"session_mode": ""}, c.RESIDENT),
            ({"runtime": "pi"}, c.UNSUPPORTED_RUNTIME),
            ({"runtime": "opencode"}, c.UNSUPPORTED_RUNTIME),
            ({"runtime": "generic"}, c.UNSUPPORTED_RUNTIME),
            ({"runtime": ""}, c.UNSUPPORTED_RUNTIME),
            ({"terminal_id": ""}, c.NO_CONSOLE),
            ({"terminal_id": "vterm_abc"}, c.NO_TUI),
            ({"terminal_command": "aify://virtual-rpc/codex"}, c.NO_TUI),
            ({"status": "working"}, c.MID_TURN),
            ({"status": "blocked"}, c.PROMPT_ON_SCREEN),
            ({"status": "offline"}, c.NOT_AT_PROMPT),
            ({"status": "available"}, c.NOT_AT_PROMPT),
            ({"status": ""}, c.NOT_AT_PROMPT),
        ]
        for overrides, code in cases:
            with self.subTest(**{k: str(v) for k, v in overrides.items()}):
                decision = decide(**overrides)
                self.assertFalse(decision.allowed)
                self.assertEqual(decision.command, "", "a refusal must carry nothing to type")
                self.assertEqual(decision.refused, code)
                self.assertTrue(decision.message.strip(), "a refusal must say why")

    def test_a_resident_refusal_says_whose_terminal_it_is(self):
        """The operator's words: typing into their own terminal is not ours. The refusal must say
        so, and name the mode that does work, or a caller retries the same thing."""
        message = decide(session_mode="resident").message
        self.assertIn("operator's own terminal", message)
        self.assertIn('"handoff"', message)

    def test_the_runtimes_with_a_native_command_are_derived_from_the_adapters(self):
        """The supported set is the adapters' own declarations, never a list here. Census over the
        whole registry so a sixth runtime is judged the day it is added, and the three that have a
        command are named so an empty census cannot pass."""
        declared = {name for name in _REGISTRY if adapter_for(name).native_compact_command}
        self.assertEqual(declared, {"claude-code", "codex", "hermes"})
        for name in _REGISTRY:
            with self.subTest(runtime=name):
                self.assertEqual(c.native_compact_command(name), adapter_for(name).native_compact_command)
        self.assertEqual(c.native_compact_command("no-such-runtime"), "")


class TranscriptLocationTests(unittest.TestCase):
    def test_claude_names_the_project_file_the_way_claude_does(self):
        """Every non-alphanumeric becomes '-': `C:\\Docker\\aify-comms` is `C--Docker-aify-comms`,
        the directory this host's own ~/.claude/projects holds for that checkout."""
        self.assertEqual(
            c.transcript_location("claude-code", "abc-123", "C:\\Docker\\aify-comms"),
            "~/.claude/projects/C--Docker-aify-comms/abc-123.jsonl",
        )
        self.assertEqual(
            c.transcript_location("claude-code", "abc", "/home/u/my_repo.v2"),
            "~/.claude/projects/-home-u-my-repo-v2/abc.jsonl",
        )

    def test_claude_past_its_truncation_names_the_file_only(self):
        """Claude hashes a directory name past 200 characters; a path built without the hash would
        send the agent to a directory that does not exist."""
        location = c.transcript_location("claude-code", "abc", "/" + "x" * 250)
        self.assertEqual(location, "abc.jsonl under ~/.claude/projects/")

    def test_codex_and_hermes_say_how_to_find_it(self):
        self.assertIn("-thread-9.jsonl", c.transcript_location("codex", "thread-9", "/w"))
        self.assertIn("$CODEX_HOME/sessions", c.transcript_location("codex", "thread-9", "/w"))
        self.assertIn("hermes sessions export <file> --session-id h-1", c.transcript_location("hermes", "h-1", "/w"))

    def test_no_handle_or_no_layout_is_empty_rather_than_invented(self):
        for runtime in ("claude-code", "codex", "hermes"):
            with self.subTest(runtime=runtime):
                self.assertEqual(c.transcript_location(runtime, "", "/w"), "")
        self.assertEqual(c.transcript_location("pi", "p-1", "/w"), "")
        self.assertEqual(c.transcript_location("no-such-runtime", "x", "/w"), "")


class HandoffBriefTests(unittest.TestCase):
    def brief(self, **overrides):
        args = dict(agent_id="coder", source_session_id="sess_1", session_handle="h-1", runtime="claude-code",
                    transcript="~/.claude/projects/p/h-1.jsonl", recent_messages=None)
        args.update(overrides)
        return c.handoff_brief(**args)

    def test_the_brief_points_at_the_history_instead_of_copying_it(self):
        """The operator's spec: the previous session id, where its transcript is, and an instruction
        to read the last N messages -- which defaults to 10."""
        text = self.brief()
        self.assertIn("Previous session: sess_1 (claude-code)", text)
        self.assertIn("Previous native session id: h-1", text)
        self.assertIn("Previous transcript: ~/.claude/projects/p/h-1.jsonl", text)
        self.assertIn('comms_inbox(agentId="coder", filter="all", limit=10, peek=true)', text)

    def test_the_count_is_the_callers_and_is_clamped(self):
        self.assertIn("limit=3,", self.brief(recent_messages=3))
        self.assertIn(f"limit={c.MAX_RECENT_MESSAGES},", self.brief(recent_messages=500))
        self.assertEqual(c.recent_messages_count(None), c.DEFAULT_RECENT_MESSAGES)
        self.assertEqual(c.recent_messages_count(""), c.DEFAULT_RECENT_MESSAGES)
        self.assertEqual(c.recent_messages_count(-4), 0)

    def test_zero_asks_for_no_reading(self):
        text = self.brief(recent_messages=0)
        self.assertNotIn("limit=", text)
        self.assertIn("No recent messages were requested", text)

    def test_unknowns_are_said_rather_than_left_blank(self):
        text = self.brief(source_session_id="", session_handle="", runtime="", transcript="")
        self.assertIn("Previous session: unknown", text)
        self.assertIn("Previous native session id: not recorded", text)
        self.assertIn("Previous transcript: location not known to aify-comms", text)


if __name__ == "__main__":
    unittest.main()
