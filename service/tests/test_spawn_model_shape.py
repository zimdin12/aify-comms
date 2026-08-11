"""A spawn's model name is validated for SHAPE at the boundary, not a day later by a wrong reaper.

THE ARTIFACT, from the live DB: `mcptest-fakemodel-claude` was spawned 2026-07-01T13:55:53Z with
`model = "totally-fake-model-9000"`. It stayed `running` for 23 hours and was finally closed by a
generic reaper reporting

    "Orphaned: claiming environment bridge is no longer live (env bridge restart/supersede)"

which was not the cause. The input was wrong on arrival; the operator got a wrong diagnosis a day
late. `mcptest-wrongmodel-claude` and `mcptest-wrongmodel-hermes` ended the same way.

WHAT IS DELIBERATELY NOT DONE HERE, because it would be worse than the bug: validating against a
list of real model names. Model names change constantly, so a stale allowlist rejects legitimate
spawns. The plausible-but-nonexistent name is covered from the other end instead — v0.2.0's
`_finalize_spawns_with_dead_terminals` + `terminal_diagnostics.py` surface the runtime's own first
fatal line when the worker exits, which is the only place that can honestly say a provider refused
a model.

So this boundary catches only what is CERTAIN rather than probable: strings that cannot be a model
name at all. Typos and bad pastes, rejected in one comparison instead of one day.
"""

from __future__ import annotations

import unittest

import pydantic

from service.models import SpawnRequestCreate

BASE = {"environmentId": "windows:host:default", "agentId": "sc-tester", "runtime": "hermes"}


def _make(model):
    return SpawnRequestCreate(**BASE, model=model)


class ModelShapeTests(unittest.TestCase):
    # ── accepted: everything that is actually used ────────────────────────────────
    def test_real_model_names_are_accepted(self):
        """Every model this deployment has actually used, from spawn_specs and agents."""
        for name in ["opus", "gpt-5.5", "claude-opus-4-8", "claude-opus-5", "sonnet",
                     "claude-haiku-4-5-20251001", "gpt-5.5-codex", "glm-5.2"]:
            with self.subTest(name):
                self.assertEqual(_make(name).model, name)

    def test_a_name_that_is_merely_WRONG_is_still_accepted(self):
        """The point of not having an allowlist: this is a well-formed name for a model that does
        not exist, and it must pass. The runtime's own error is the honest source for that, surfaced
        by terminal_diagnostics when the worker exits."""
        self.assertEqual(_make("totally-fake-model-9000").model, "totally-fake-model-9000")

    def test_omitted_and_blank_both_mean_runtime_default(self):
        self.assertIsNone(SpawnRequestCreate(**BASE).model)
        self.assertIsNone(_make(None).model)
        self.assertIsNone(_make("").model)
        self.assertIsNone(_make("   ").model)

    def test_surrounding_whitespace_is_trimmed_not_rejected(self):
        self.assertEqual(_make("  opus  ").model, "opus")

    # ── rejected: strings that cannot be a model name ────────────────────────────
    def test_a_space_INSIDE_the_name_is_rejected(self):
        """The typo that motivated this: "opus 5" reaches the CLI as two arguments."""
        with self.assertRaises(pydantic.ValidationError) as caught:
            _make("opus 5")
        self.assertIn("single token", str(caught.exception))

    def test_an_INTERNAL_newline_or_tab_is_rejected(self):
        for hostile in ["opus\nsonnet", "opus\ttab", "opus\rsonnet"]:
            with self.subTest(repr(hostile)):
                with self.assertRaises(pydantic.ValidationError):
                    _make(hostile)

    def test_a_TRAILING_newline_is_trimmed_rather_than_rejected(self):
        """The distinction the first version of this test got wrong. A trailing CRLF is what a paste
        from a terminal or a text file carries; rejecting it would be hostile for no benefit, while
        an INTERNAL newline genuinely cannot be one CLI argument."""
        self.assertEqual(_make("opus\r\n").model, "opus")
        self.assertEqual(_make("\ngpt-5.5\n").model, "gpt-5.5")

    def test_shell_metacharacters_are_rejected(self):
        """A model name becomes an argument to a runtime CLI. These do not belong in one, and
        rejecting them at the boundary is cheaper than reasoning about every launch path."""
        for hostile in ["opus; rm -rf /", "opus && echo", "opus|tee", "$(id)", "`id`", "opus>out"]:
            with self.subTest(hostile):
                with self.assertRaises(pydantic.ValidationError):
                    _make(hostile)

    def test_an_absurd_length_is_rejected(self):
        with self.assertRaises(pydantic.ValidationError) as caught:
            _make("m" * 500)
        self.assertIn("not a model name", str(caught.exception))

    def test_the_error_says_what_a_model_name_looks_like(self):
        """A rejection an operator cannot act on just moves the confusion earlier."""
        with self.assertRaises(pydantic.ValidationError) as caught:
            _make("claude opus 5")
        message = str(caught.exception)
        self.assertIn("opus", message)
        self.assertIn("gpt-5.5", message)


if __name__ == "__main__":
    unittest.main()
