"""A Reset must not hand the launcher the session it just discarded.

Observed 2026-09-08: a dashboard Reset on a hermes agent wrote `resumePolicy: fresh_context` and
cleared the stored handle, and the launch env STILL carried the old handle as AIFY_SESSION_HANDLE and
HERMES_SESSION_ID -- the agent row had been refilled by the old worker's heartbeat, and this composer
read the row. The launcher resumed a session pinned to a retired model, which died 16s after
agent_init while the dashboard reported the agent online. The flag and the handle must agree.
"""
from __future__ import annotations

import unittest

from service.api_core.launch_env import managed_launch_env, session_env_vars_for

OLD = "20260715_085656_69c4dd"


def _launch(resume_policy: str) -> dict[str, str]:
    agent = {"id": "acma-coder", "sessionHandle": OLD, "runtimeState": {"resumePolicy": resume_policy}}
    terminal = {"agentId": "acma-coder", "runtime": "hermes", "sessionHandle": OLD}
    return managed_launch_env(terminal=terminal, agent=agent, workspace="/home/dev/projects/x", terminal_id="term_1")


class FreshContextLaunch(unittest.TestCase):
    def test_a_fresh_context_launch_carries_the_flag_and_NO_handle(self):
        env = _launch("fresh_context")
        self.assertEqual(env["AIFY_HERMES_FRESH_CONTEXT"], "1")
        self.assertEqual(env["AIFY_SESSION_HANDLE"], "")
        for name in session_env_vars_for("hermes"):
            self.assertEqual(env.get(name, ""), "", f"{name} must not carry the discarded session")

    def test_a_native_first_launch_still_carries_the_stored_handle(self):
        # The control: the same inputs with the ordinary policy keep resuming.
        env = _launch("native_first")
        self.assertEqual(env["AIFY_HERMES_FRESH_CONTEXT"], "")
        self.assertEqual(env["AIFY_SESSION_HANDLE"], OLD)
        for name in session_env_vars_for("hermes"):
            self.assertEqual(env.get(name), OLD)


if __name__ == "__main__":
    unittest.main()
