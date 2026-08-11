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


class EveryIngressTests(unittest.TestCase):
    """REWORK from review: I attached the validator to one request model and called it "every path".

    The probe that broke that claim: `AgentEnvironmentAssignRequest(model="opus;rm")` was accepted
    verbatim and written to `spawn_specs.model`, from where the next spawn reads it. A validator on
    one of three doors is not a boundary.

    These tests exist to make the completeness claim testable rather than asserted. If a NEW request
    model or settings key can carry a model, add it here in the same commit.
    """

    def test_the_environment_assign_path_rejects_the_same_shapes(self):
        from service.models import AgentEnvironmentAssignRequest

        for hostile in ["opus 5", "opus;rm", "opus\nsonnet", "$(id)", "m" * 500]:
            with self.subTest(hostile):
                with self.assertRaises(pydantic.ValidationError):
                    AgentEnvironmentAssignRequest(environmentId="e", model=hostile)

    def test_the_environment_assign_path_still_accepts_real_names(self):
        from service.models import AgentEnvironmentAssignRequest

        self.assertEqual(AgentEnvironmentAssignRequest(environmentId="e", model=" opus ").model, "opus")
        self.assertIsNone(AgentEnvironmentAssignRequest(environmentId="e").model)

    def test_one_definition_of_the_rule_for_every_rejecting_ingress(self):
        """Three doors with three copies of "that is not a model name" would drift. Same helper."""
        from service.models import SpawnRequestCreate, AgentEnvironmentAssignRequest, validate_model_shape

        for hostile in ["opus 5", "a`b", "x" * 300]:
            with self.subTest(hostile):
                with self.assertRaises(ValueError):
                    validate_model_shape(hostile)
                with self.assertRaises(pydantic.ValidationError):
                    SpawnRequestCreate(**BASE, model=hostile)
                with self.assertRaises(pydantic.ValidationError):
                    AgentEnvironmentAssignRequest(environmentId="e", model=hostile)


class SelfReportIngressTests(unittest.TestCase):
    """The FOURTH ingress, which review did not name and which must NOT reject.

    `AgentRegister.model` is an agent's report about the runtime it is already running, and it
    reaches a CLI via `agentInfo.model` -> `AIFY_MANAGED_MODEL`. But an agent that cannot register
    is dead — no inbox, no dispatch, no status — so trading a live agent for a bad model string is
    worse than the bug. Unusable values are DROPPED, which means "unknown model, use the runtime
    default", not repaired into a confident guess nobody can trace.
    """

    def test_registration_survives_an_unusable_model(self):
        from service.models import AgentRegister

        for hostile in ["opus 5", "opus;rm", "m" * 500]:
            with self.subTest(hostile):
                agent = AgentRegister(agentId="a", role="coder", model=hostile)
                self.assertIsNone(agent.model, "an unusable self-reported model becomes unknown")

    def test_a_NON_ASCII_single_token_is_kept_everywhere(self):
        """My first draft of the test above expected "日本語" to be dropped. It is wrong to drop it:
        it is one token with no forbidden characters, so it reaches a CLI as one argument perfectly
        well. The rule is "can this be a single argument", not "is this English" — a provider is free
        to name a model in any script, and rejecting that would be a bug in this validator rather
        than protection."""
        from service.models import AgentRegister

        self.assertEqual(AgentRegister(agentId="a", role="coder", model="日本語").model, "日本語")
        self.assertEqual(_make("модель-1").model, "модель-1")

    def test_a_good_self_reported_model_is_kept(self):
        from service.models import AgentRegister

        self.assertEqual(AgentRegister(agentId="a", role="coder", model=" gpt-5.5 ").model, "gpt-5.5")

    def test_it_is_dropped_not_REPAIRED(self):
        """Repairing "opus 5" into "opus5" would launch a runtime with a model nobody chose."""
        from service.models import AgentRegister

        self.assertIsNone(AgentRegister(agentId="a", role="coder", model="opus 5").model)



class FifthDoorTests(unittest.TestCase):
    """`runtimeConfig.model` — the door I did not enumerate when I called four of them a boundary.

    Found by an external review of the release ladder, 2026-08-11, and it is my own thesis turned
    back on me: `mcp/stdio/terminal-env.js` reads `runtimeConfig.model` as the FALLBACK for
    `AIFY_MANAGED_MODEL` and for the managed CODEX_HOME it prepares, so a free-form dict beside a
    validated scalar carried the exact payload the scalar refuses.

    Reproduced before the fix:
        SpawnRequestCreate(runtimeConfig={"model": "opus; rm -rf /"})  ->  accepted verbatim

    The lesson recorded here is not "there was a fifth check to add" — it is that a free-form dict
    next to a validated field is a hole by construction. Any NEW field that can carry a model must
    be added to this class in the same commit.
    """

    HOSTILE = ["opus; rm -rf /", "opus 5", "$(id)", "`id`", "opus\nsonnet", "m" * 500]

    def test_the_spawn_path_rejects_a_hostile_runtime_config_model(self):
        for hostile in self.HOSTILE:
            with self.subTest(hostile):
                with self.assertRaises(pydantic.ValidationError):
                    SpawnRequestCreate(**BASE, runtimeConfig={"model": hostile})

    def test_the_assign_path_rejects_it_too(self):
        from service.models import AgentEnvironmentAssignRequest

        for hostile in self.HOSTILE:
            with self.subTest(hostile):
                with self.assertRaises(pydantic.ValidationError):
                    AgentEnvironmentAssignRequest(environmentId="e", runtimeConfig={"model": hostile})

    def test_a_good_runtime_config_survives_intact(self):
        cfg = SpawnRequestCreate(**BASE, runtimeConfig={"model": "opus", "effort": "high"}).runtimeConfig
        self.assertEqual(cfg, {"model": "opus", "effort": "high"})

    def test_a_runtime_config_without_a_model_is_untouched(self):
        """The dict is free-form by design — validating one key must not start policing the rest."""
        cfg = {"effort": "xhigh", "thinking": "deep", "anything": [1, 2, 3]}
        self.assertEqual(SpawnRequestCreate(**BASE, runtimeConfig=cfg).runtimeConfig, cfg)

    def test_an_empty_model_key_is_dropped_not_kept_as_empty(self):
        cfg = SpawnRequestCreate(**BASE, runtimeConfig={"model": "  ", "effort": "high"}).runtimeConfig
        self.assertEqual(cfg, {"effort": "high"}, "an empty model means 'runtime default', i.e. absent")

    def test_self_report_drops_the_model_key_and_keeps_the_rest(self):
        """Registration must never fail over a model string — the same asymmetry as the scalar."""
        from service.models import AgentRegister

        agent = AgentRegister(agentId="a", role="coder",
                              runtimeConfig={"model": "opus; rm", "effort": "high"})
        self.assertEqual(agent.runtimeConfig, {"effort": "high"})

    def test_self_report_keeps_a_good_model(self):
        from service.models import AgentRegister

        agent = AgentRegister(agentId="a", role="coder",
                              runtimeConfig={"model": " gpt-5.5 ", "effort": "high"})
        self.assertEqual(agent.runtimeConfig, {"model": "gpt-5.5", "effort": "high"})

    def test_a_non_dict_runtime_config_does_not_crash_the_validator(self):
        for junk in [[], "opus", 17]:
            with self.subTest(junk):
                # Pydantic rejects the wrong TYPE; the validator must not be what raises.
                with self.assertRaises(pydantic.ValidationError):
                    SpawnRequestCreate(**BASE, runtimeConfig=junk)


if __name__ == "__main__":
    unittest.main()
