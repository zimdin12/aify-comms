""""Why can't work reach this agent, and what do I do about it?" — the nine answers.

`service/api_core/dispatch_hint.py` is named by no test file. It is a single pure function that turns
a refusal into operator-facing prose, and its own docstring says why every branch exists: a preflight
that refuses a send without naming the missing capability, runtime or session mode sends the operator
to the logs.

WHICH MAKES ITS FAILURE MODE UNUSUAL. Nothing breaks when this is wrong. The send is refused either
way; what changes is whether the person reading the refusal is told to restart a wrapper, spawn a
managed agent, or go and look at the logs. A hint that names the wrong fix is worse than no hint,
because it is followed.

THE ORDER IS PART OF THE ANSWER. Several agents match more than one branch — a resident claude with
no `resident-run` capability whose refusal mentions a resident bridge matches two, a codex resident
with no handle matches two — and the earlier branch wins. That is not incidental: the reason string
describes what actually failed just now, and it outranks a standing configuration fact. The
overlapping cases are tested as overlaps rather than each branch being tested only in isolation,
because isolation is exactly the condition under which an ordering bug does not show.

ONE COUPLING IS PINNED DELIBERATELY: the first branch keys on `"resident bridge" in reason`, a
substring of prose composed in `send_preflight.py`. Rewording that message there silently drops every
agent it describes into the generic fallback, and nothing else in the suite would notice.
"""

from __future__ import annotations

import unittest

from service.api_core.dispatch_hint import _dispatch_fix_hint

RESIDENT_BRIDGE_REASON = (
    "resident bridge heartbeat is gone; restart the resident wrapper or switch to managed"
)


def row(*, runtime="claude-code", session_mode="resident", role="coder", capabilities="[]",
        session_handle="", launch_mode="detached", **extra) -> dict:
    """An agents row as `SELECT *` hands it over."""
    base = {
        "id": "recipient",
        "runtime": runtime,
        "session_mode": session_mode,
        "role": role,
        "capabilities": capabilities,
        "session_handle": session_handle,
        "launch_mode": launch_mode,
        "runtime_config": "{}",
    }
    base.update(extra)
    return base


class AlwaysPresentTests(unittest.TestCase):
    """The identifying fields, on every branch. They are what the operator reads first."""

    CASES = {
        "unregistered": (None, "agent is not registered"),
        "resident-bridge-gone": (row(), RESIDENT_BRIDGE_REASON),
        "codex-no-handle": (row(runtime="codex"), "agent status is \"offline\""),
        "opencode-resident": (row(runtime="opencode"), "agent is working"),
        "unlaunchable": (row(runtime="generic"), "agent is working"),
        "managed-no-launch": (row(session_mode="managed", launch_mode="none"), "agent is working"),
        "fallback": (row(runtime="hermes", session_mode="managed"), "agent is working"),
    }

    def test_every_branch_reports_the_target_and_the_reason(self):
        """The reason is passed through verbatim. A hint that paraphrased it would leave the
        operator unable to match the explanation to the refusal they actually saw."""
        for name, (agent_row, reason) in self.CASES.items():
            with self.subTest(case=name):
                hint = _dispatch_fix_hint("recipient", agent_row, reason)
                self.assertEqual(hint["targetAgentId"], "recipient")
                self.assertEqual(hint["reason"], reason)

    def test_every_branch_reports_the_runtime_session_mode_and_capabilities(self):
        """The three facts that decide whether an agent can be driven. They are in every hint so a
        wrong-looking fix can be checked against the state it was derived from.

        Asserted by VALUE against the row, not by key presence — my first version checked only that
        the keys existed, and mutations that hardcoded `sessionMode` to "resident" and `capabilities`
        to `[]` both survived it. A field reported as a constant is worse than an absent one: it
        looks like evidence."""
        for name, (agent_row, reason) in self.CASES.items():
            with self.subTest(case=name):
                hint = _dispatch_fix_hint("recipient", agent_row, reason)
                expected_mode = agent_row["session_mode"] if agent_row else "resident"
                expected_runtime = agent_row["runtime"] if agent_row else "generic"
                self.assertEqual(hint["sessionMode"], expected_mode)
                self.assertEqual(hint["runtime"], expected_runtime)
                self.assertIn("capabilities", hint)

    def test_the_reported_CAPABILITIES_are_the_agents_own(self):
        """The list an operator checks against the fix they were given — "it says restart the
        wrapper, and indeed there is no resident-run". A hint that always reports an empty list
        makes every agent look broken in the same way."""
        hint = _dispatch_fix_hint(
            "recipient",
            row(capabilities='["resident-run", "steer"]', runtime_config='{"channelEnabled": true}'),
            "agent is working",
        )
        self.assertIn("resident-run", hint["capabilities"])
        self.assertIn("steer", hint["capabilities"])

    def test_the_reported_capabilities_are_the_READ_TIME_ones(self):
        """`_row_capabilities`, not the stored column: a resident claude with no channel has
        `resident-run` stripped, and reporting the stale stored list would contradict the very fix
        the hint is about to suggest."""
        hint = _dispatch_fix_hint(
            "recipient", row(capabilities='["resident-run", "steer"]'), "agent is working")
        self.assertNotIn("resident-run", hint["capabilities"])

    def test_every_branch_says_what_to_do(self):
        for name, (agent_row, reason) in self.CASES.items():
            with self.subTest(case=name):
                hint = _dispatch_fix_hint("recipient", agent_row, reason)
                self.assertTrue(str(hint.get("fix") or "").strip(), "a hint with no fix")

    def test_every_branch_that_suggests_COMMANDS_includes_the_inspect_one(self):
        """`comms_agent_info` is the command that shows whether the fix worked. A branch that
        suggests a repair without it leaves the operator guessing at the result."""
        for name, (agent_row, reason) in self.CASES.items():
            with self.subTest(case=name):
                commands = _dispatch_fix_hint("recipient", agent_row, reason).get("suggestedCommands")
                if commands is None:
                    continue
                self.assertTrue(
                    any("comms_agent_info" in command for command in commands), commands,
                )


class UnregisteredTests(unittest.TestCase):
    def test_an_unregistered_target_is_told_to_register(self):
        hint = _dispatch_fix_hint("ghost", None, "agent is not registered")
        self.assertIn("Register the target agent first", hint["fix"])

    def test_it_suggests_NO_COMMANDS_AT_ALL(self):
        """Every other branch interpolates the id into a command string. This is the ONE branch
        reachable with an id that no registration ever validated — the row is None precisely because
        nothing by that name exists — and it returns before any command is built. Absent, not empty:
        there is nothing to run until the agent exists."""
        self.assertNotIn("suggestedCommands", _dispatch_fix_hint("gh\"ost", None, "unregistered"))

    def test_it_still_reports_the_defaults_for_the_missing_row(self):
        """`generic` / `resident` are what a missing row reads as, and they are reported rather than
        omitted so the shape of a hint never depends on whether the agent existed."""
        hint = _dispatch_fix_hint("ghost", None, "agent is not registered")
        self.assertEqual(hint["runtime"], "generic")
        self.assertEqual(hint["sessionMode"], "resident")
        self.assertEqual(hint["capabilities"], [])


class ResidentBridgeTests(unittest.TestCase):
    """The first branch, and the only one keyed on the REASON rather than on state."""

    def test_a_resident_agent_whose_bridge_is_gone_is_told_to_restart_the_wrapper(self):
        hint = _dispatch_fix_hint("r1", row(), RESIDENT_BRIDGE_REASON)
        self.assertIn("Restart the visible resident wrapper", hint["fix"])

    def test_it_names_the_RUNTIME_in_words(self):
        """"Restart the visible resident wrapper for this Claude session" is followable; "for this
        claude-code session" reads like an internal identifier the operator has to decode."""
        for runtime, spoken in (("claude-code", "Claude"), ("codex", "Codex"), ("hermes", "Hermes"),
                                ("opencode", "OpenCode"), ("pi", "Oh My Pi")):
            with self.subTest(runtime=runtime):
                hint = _dispatch_fix_hint("r1", row(runtime=runtime), RESIDENT_BRIDGE_REASON)
                self.assertIn(spoken, hint["fix"])

    def test_an_unknown_runtime_falls_back_to_its_own_name(self):
        hint = _dispatch_fix_hint("r1", row(runtime="generic"), RESIDENT_BRIDGE_REASON)
        self.assertIn("generic", hint["fix"])

    def test_it_warns_that_a_METADATA_UPDATE_is_not_a_registration(self):
        """The mistake this branch was written for: an operator PATCHes the agent row, sees the
        fields change, and concludes the agent is connected. The row is not the bridge."""
        hint = _dispatch_fix_hint("r1", row(), RESIDENT_BRIDGE_REASON)
        self.assertIn("do not create the resident bridge heartbeat", hint["fix"])

    def test_the_suggested_registration_carries_the_agents_OWN_role_and_runtime(self):
        """A copy-pasteable command. Suggesting defaults instead would silently re-register the
        agent as something else — the fix that breaks the thing it repaired."""
        hint = _dispatch_fix_hint("r1", row(runtime="hermes", role="tester"), RESIDENT_BRIDGE_REASON)
        joined = " ".join(hint["suggestedCommands"])
        self.assertIn('agentId="r1"', joined)
        self.assertIn('role="tester"', joined)
        self.assertIn('runtime="hermes"', joined)

    def test_a_MANAGED_agent_does_not_get_the_resident_advice(self):
        """The branch requires both the reason AND resident mode. Telling the owner of a managed
        agent to restart a visible wrapper describes a terminal that does not exist."""
        hint = _dispatch_fix_hint("m1", row(session_mode="managed"), RESIDENT_BRIDGE_REASON)
        self.assertNotIn("Restart the visible resident wrapper", hint["fix"])

    def test_the_branch_keys_on_a_SUBSTRING_of_prose_written_elsewhere(self):
        """Pinned because it is fragile by construction: the trigger is `"resident bridge" in
        reason`, and the message it matches is composed in `send_preflight.py`. Reword it there and
        every agent it describes drops into the generic fallback with nothing failing."""
        self.assertIn("resident bridge", RESIDENT_BRIDGE_REASON)
        matched = _dispatch_fix_hint("r1", row(), "the resident bridge is gone")
        self.assertIn("Restart the visible resident wrapper", matched["fix"])
        missed = _dispatch_fix_hint("r1", row(), "the resident connection is gone")
        self.assertNotIn("Restart the visible resident wrapper", missed["fix"])


class PerRuntimeResidentTests(unittest.TestCase):
    def test_a_resident_CODEX_with_no_session_handle_is_told_to_restart_codex(self):
        """No handle means nothing identifies which live session to wake, so re-registering from
        the intended one is the whole fix."""
        hint = _dispatch_fix_hint("c1", row(runtime="codex", session_handle=""), "agent is offline")
        self.assertIn("Restart Codex", hint["fix"])

    def test_a_resident_codex_WITH_a_handle_does_not_get_that_advice(self):
        hint = _dispatch_fix_hint(
            "c1", row(runtime="codex", session_handle="thread-1"), "agent is offline")
        self.assertNotIn("Restart Codex", hint["fix"])

    def test_a_resident_CLAUDE_without_resident_run_is_told_to_start_with_the_wrapper(self):
        """Plain `claude` registers an agent that cannot be woken. The capability is the evidence,
        and `claude-aify` is the fix."""
        hint = _dispatch_fix_hint("cl1", row(capabilities="[]"), "agent is offline")
        self.assertIn("claude-aify", hint["fix"])
        self.assertIn("claude-aify", hint["suggestedCommands"])

    def test_a_resident_claude_that_HAS_resident_run_gets_the_generic_advice(self):
        hint = _dispatch_fix_hint(
            "cl1",
            row(capabilities='["resident-run"]', runtime_config='{"channelEnabled": true}'),
            "agent is offline",
        )
        self.assertNotIn("claude-aify", hint["fix"])

    def test_resident_OPENCODE_is_presence_only_and_must_be_SPAWNED(self):
        """Not a repair — a redirection. There is no way to make a resident opencode session take
        work, so the hint offers the thing that does."""
        hint = _dispatch_fix_hint("o1", row(runtime="opencode"), "agent is offline")
        self.assertIn("presence-only", hint["fix"])
        self.assertTrue(any("comms_spawn" in c for c in hint["suggestedCommands"]))

    def test_resident_PI_is_presence_only_and_must_be_SPAWNED(self):
        hint = _dispatch_fix_hint("p1", row(runtime="pi"), "agent is offline")
        self.assertIn("presence-only", hint["fix"])
        self.assertTrue(any("comms_spawn" in c for c in hint["suggestedCommands"]))

    def test_the_spawn_suggestion_does_not_reuse_the_TARGETS_OWN_ID(self):
        """The spawned agent is a NEW one — suggesting the same id would collide with the
        presence-only session that is already registered under it.

        BOTH presence-only branches, because they are separate copies of the same advice: my first
        version tested only pi, and the mutation that reused the target id in the OPENCODE branch
        survived it."""
        for runtime in ("pi", "opencode"):
            with self.subTest(runtime=runtime):
                commands = _dispatch_fix_hint(
                    "p1", row(runtime=runtime), "offline")["suggestedCommands"]
                spawn = next(c for c in commands if "comms_spawn" in c)
                self.assertIn('agentId="p1-teammate"', spawn)
                self.assertIn(f'runtime="{runtime}"', spawn)

    def test_MANAGED_opencode_and_pi_are_not_presence_only(self):
        """The presence-only branches are resident-scoped. A managed one of either is exactly the
        thing the resident hint tells the operator to go and create."""
        for runtime in ("opencode", "pi"):
            with self.subTest(runtime=runtime):
                hint = _dispatch_fix_hint(
                    "x1", row(runtime=runtime, session_mode="managed"), "agent is offline")
                self.assertNotIn("presence-only", hint["fix"])


class UnlaunchableAndManagedTests(unittest.TestCase):
    def test_a_runtime_NOTHING_CAN_LAUNCH_is_message_only(self):
        """A dashboard agent, or a runtime the fleet has no launcher for. The hint's job here is to
        stop the operator following restart advice for a thing that was never startable — it says so
        explicitly rather than staying silent."""
        hint = _dispatch_fix_hint("g1", row(runtime="generic", session_mode="managed"), "offline")
        self.assertIn("message-only", hint["fix"])
        self.assertIn("before suggesting any runtime-specific reinstall", hint["fix"])

    def test_every_LAUNCHABLE_runtime_escapes_that_branch(self):
        """A census against the vocabulary rather than a copied list: adding a runtime to
        `LAUNCHABLE_RUNTIMES` without a branch here would otherwise be reported as message-only."""
        from service.api_core.vocabulary import LAUNCHABLE_RUNTIMES

        for runtime in sorted(LAUNCHABLE_RUNTIMES):
            with self.subTest(runtime=runtime):
                hint = _dispatch_fix_hint(
                    "l1", row(runtime=runtime, session_mode="managed"), "agent is working")
                self.assertNotIn("message-only", hint["fix"])

    def test_a_managed_agent_with_launch_mode_NONE_is_told_to_enable_it(self):
        hint = _dispatch_fix_hint(
            "m1", row(runtime="hermes", session_mode="managed", launch_mode="none"), "offline")
        self.assertIn("Enable launch mode", hint["fix"])

    def test_a_managed_agent_with_a_real_launch_mode_gets_the_generic_advice(self):
        for launch_mode in ("detached", "auto", ""):
            with self.subTest(launch_mode=launch_mode):
                hint = _dispatch_fix_hint(
                    "m1", row(runtime="hermes", session_mode="managed", launch_mode=launch_mode),
                    "offline")
                self.assertNotIn("Enable launch mode", hint["fix"])

    def test_a_RESIDENT_agent_is_never_told_to_enable_launch_mode(self):
        """Launch mode is a managed concept. The branch is mode-scoped, and without that scope a
        resident agent would be sent to change a setting that does not apply to it."""
        hint = _dispatch_fix_hint(
            "r1", row(runtime="hermes", session_mode="resident", launch_mode="none"), "offline")
        self.assertNotIn("Enable launch mode", hint["fix"])

    def test_the_last_resort_says_INSPECT_rather_than_inventing_a_repair(self):
        """When no branch recognises the situation the honest answer is "go and look". A generic
        restart instruction here would be advice generated from no evidence."""
        hint = _dispatch_fix_hint("h1", row(runtime="hermes", session_mode="managed"), "working")
        self.assertIn("Inspect the target runtime/session", hint["fix"])
        self.assertEqual(hint["suggestedCommands"], ['comms_agent_info(agentId="h1")'])


class BranchOrderTests(unittest.TestCase):
    """Overlaps. Each of these rows matches two branches, and the earlier one has to win."""

    def test_the_REASON_outranks_a_missing_codex_handle(self):
        """A resident codex with no handle also has a dead bridge. The bridge is what failed NOW,
        and restarting the wrapper is a superset of what the codex advice asks for."""
        hint = _dispatch_fix_hint(
            "c1", row(runtime="codex", session_handle=""), RESIDENT_BRIDGE_REASON)
        self.assertIn("Restart the visible resident wrapper", hint["fix"])
        self.assertNotIn("Restart Codex", hint["fix"])

    def test_the_REASON_outranks_a_missing_claude_capability(self):
        hint = _dispatch_fix_hint("cl1", row(capabilities="[]"), RESIDENT_BRIDGE_REASON)
        self.assertIn("Restart the visible resident wrapper", hint["fix"])
        self.assertNotIn("claude-aify", hint["fix"])

    def test_the_REASON_outranks_the_presence_only_redirect(self):
        hint = _dispatch_fix_hint("p1", row(runtime="pi"), RESIDENT_BRIDGE_REASON)
        self.assertIn("Restart the visible resident wrapper", hint["fix"])
        self.assertNotIn("presence-only", hint["fix"])

    def test_presence_only_outranks_the_unlaunchable_branch_for_pi(self):
        """`pi` IS launchable, so this is really a check that the ordering is not accidental: the
        specific redirect must be reached before any generic classification."""
        hint = _dispatch_fix_hint("p1", row(runtime="pi"), "agent is offline")
        self.assertIn("presence-only", hint["fix"])

    def test_an_unlaunchable_MANAGED_agent_is_message_only_before_launch_mode(self):
        """Both branches match a managed generic agent with launch_mode none. "Nothing can launch
        this" is the truer statement — enabling launch mode on it would fix nothing."""
        hint = _dispatch_fix_hint(
            "g1", row(runtime="generic", session_mode="managed", launch_mode="none"), "offline")
        self.assertIn("message-only", hint["fix"])
        self.assertNotIn("Enable launch mode", hint["fix"])


if __name__ == "__main__":
    unittest.main()
