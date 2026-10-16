"""The 0.9.8 rule every flipped wake-refusal test now asserts, in one place.

Steven, 2026-10-16: a woken send to an agent that cannot start is kept in its inbox and the answer says the
wake failed. Before, it was refused and nothing was written; the tests that pinned that refusal now pin
this instead, and keep their own other guarantees (no cold start, no migration, no control) unchanged.
"""


def assert_stored_unwoken(case, sent, agent_id):
    """Stored with an id, no run for `agent_id`, listed as not started, and the notice names the failed wake."""
    case.assertTrue(sent.get("ok"), sent)
    case.assertTrue(sent.get("stored"), sent)
    case.assertTrue(sent.get("messageId"), sent)
    runs = [run for run in (sent.get("dispatchRuns") or []) if run.get("targetAgentId") == agent_id]
    case.assertEqual(runs, [], f"{agent_id} could not start, so it gets no run: {sent}")
    case.assertIn(agent_id, [hint.get("targetAgentId") for hint in sent.get("notStarted") or []], sent)
    case.assertIn(f"Wake failed for {agent_id}", sent.get("notice") or "", sent)
