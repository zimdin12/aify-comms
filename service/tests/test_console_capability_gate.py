"""Two refusals that must not collapse into one, and nothing called the function that makes them.

`_refuse_console_without_terminal_capability` is the 409 an operator gets when a Console cannot be
started. Its own docstring says why it exists in that shape:

    THE MESSAGES ARE THE POINT. Both branches refuse with 409, and an operator reading only the
    status code learns nothing they can act on. ... whole-environment PTY capability being off is a
    HOST problem (node-pty is not installed or built for that bridge, and the Console is dead there
    for every runtime), while an advertised-runtimes miss is a SELECTION problem (the host is fine,
    this runtime is not on its list). Those have different fixes, and collapsing them into one "not
    supported" sent operators to reinstall a bridge that was working.

So the distinction is the feature, the failure it was written from is a wasted reinstall, and no test
called it. This is the same class as the cold-start refusals that told an operator to start a bridge
that was already running (a32efd96 / 5417dcb1): a message that names a cause the code did not check.

Nothing here needs a database or an app — the function takes two plain mappings and either returns
or raises.
"""

from __future__ import annotations

import unittest

from fastapi import HTTPException

from service.api_core.console_capability_gate import _refuse_console_without_terminal_capability


def _env(**overrides):
    base = {
        "id": "env-1",
        "terminal": True,
        "pty": True,
        "terminalRuntimes": ["claude-code", "hermes"],
    }
    base.update(overrides)
    return base


def _session(runtime="claude-code"):
    return {"runtime": runtime}


def _refusal(environment, session) -> str:
    try:
        _refuse_console_without_terminal_capability(environment, session)
    except HTTPException as exc:
        assert exc.status_code == 409, f"expected 409, got {exc.status_code}"
        return str(exc.detail)
    raise AssertionError("expected a refusal, but the gate let the start through")


class ConsoleCapabilityGateTests(unittest.TestCase):
    # ── the gate lets a capable environment through ──────────────────────────────────────────

    def test_a_capable_environment_is_not_refused(self):
        """Anti-vacuity for everything below: a gate that always raised would satisfy each
        per-branch assertion about message content."""
        self.assertIsNone(
            _refuse_console_without_terminal_capability(_env(), _session("claude-code"))
        )

    def test_an_empty_advertised_list_means_no_restriction(self):
        """`terminalRuntimes: []` is "not advertised", not "nothing allowed" — the predicate only
        restricts when the list is non-empty. A bridge that reports no list still hosts consoles."""
        self.assertIsNone(
            _refuse_console_without_terminal_capability(
                _env(terminalRuntimes=[]), _session("codex")
            )
        )

    # ── HOST problem: the bridge has no PTY at all ──────────────────────────────────────────

    def test_missing_pty_capability_is_reported_as_a_host_problem(self):
        for missing in ("terminal", "pty"):
            with self.subTest(missing=missing):
                detail = _refusal(_env(**{missing: False}), _session("claude-code"))
                self.assertIn("no PTY/terminal capability", detail)
                self.assertIn("node-pty", detail, "the operator needs the actual fix named")
                self.assertIn(
                    "ALL runtimes", detail,
                    "the point of this branch is that the host is dead for every runtime, not just "
                    "the requested one — without that an operator hunts a per-runtime problem",
                )
                self.assertIn("env-1", detail, "which environment must be identifiable")

    def test_the_host_branch_reports_the_flags_it_saw(self):
        detail = _refusal(_env(terminal=True, pty=False), _session("hermes"))
        self.assertIn("terminal=True", detail)
        self.assertIn("pty=False", detail)

    # ── SELECTION problem: the host is fine, this runtime is not on its list ────────────────

    def test_an_unadvertised_runtime_is_reported_as_a_selection_problem(self):
        detail = _refusal(_env(), _session("codex"))
        self.assertIn("supports the Console but not for runtime", detail)
        self.assertIn("codex", detail, "the refused runtime must be named")
        self.assertIn("claude-code, hermes", detail, "...alongside what IS advertised")
        self.assertNotIn(
            "node-pty", detail,
            "this branch must NOT send the operator to reinstall a bridge that is working — that is "
            "the wasted reinstall the split exists to prevent",
        )

    def test_an_advertised_list_that_is_present_but_unusable_still_names_it(self):
        detail = _refusal(_env(terminalRuntimes=["pi"]), _session("opencode"))
        self.assertIn("pi", detail)

    # ── the two branches must stay distinguishable ──────────────────────────────────────────

    def test_the_host_and_selection_refusals_are_different_messages(self):
        """The whole reason this function is not one branch. If these ever converge, an operator
        cannot tell "your bridge is broken" from "pick another runtime" — and the recorded cost of
        that confusion was reinstalling a working bridge."""
        host = _refusal(_env(pty=False), _session("codex"))
        selection = _refusal(_env(), _session("codex"))
        self.assertNotEqual(host, selection)
        self.assertIn("node-pty", host)
        self.assertNotIn("node-pty", selection)

    def test_a_host_failure_wins_over_a_selection_failure(self):
        """Both wrong at once: no PTY AND an unadvertised runtime. The host problem must be
        reported, because fixing the selection would not help until the bridge has a PTY."""
        detail = _refusal(_env(pty=False, terminalRuntimes=["pi"]), _session("codex"))
        self.assertIn("no PTY/terminal capability", detail)
        self.assertNotIn("supports the Console but not for runtime", detail)
