"""A cause aify-comms invented must never be reported as a cause a provider confirmed.

THE INCIDENT (2026-08-18, reported by graph-tech-lead, confirmed by inspection). A sender was told:

    "graph-senior-dev-hermes couldn't respond — its model provider is rate-limiting / at a usage limit
     right now (a provider-side throttle, not your request). Please retry shortly."

The run record said something weaker and different: the turn was PRESUMED dead, with three named
possibilities. The real cause was a fourth thing nobody had enumerated — the provider refused the turn
on safety grounds. The wrong cause travelled through a tech lead into a human-facing status report,
and the human had to correct it.

THE MECHANISM, which is the part worth testing. Nothing chose that wording for that run:

  * `dispatch_lifecycle` wrote "...presumed dead (model 429, mid-turn interrupt, or stall)...";
  * `_is_provider_rate_limit_error` ends with `re.search(r"\\b(429|529)\\b", text)`;
  * so the classifier matched the literal "429" INSIDE OUR OWN LIST OF GUESSES and returned True;
  * and the notification restated it in the present indicative, adding "not your request" — a
    confidence claim about a cause nothing had determined.

The word boundary in that regex was added on purpose, so `code 4290` would not false-positive. (Its
comment also claimed `529 tokens` was excluded; measured, it is not — a space is a word boundary. That
comment is corrected as part of this change, and the true behaviour is pinned below.) The predicate was
careful and correct for its intended input: text a PROVIDER produced.
It was then asked the wrong question. **No amount of care inside a predicate protects it from being
handed the wrong input**, which is why the fix is a provenance check and not a better regex.

WHY REWORDING WOULD NOT HAVE BEEN A FIX. Deleting "429" from the reconciler's string ends this
incident and leaves the mechanism armed for the next service-authored reason that mentions a status
code — and there are eight-plus such reason strings across the reconcilers. The gate below therefore
sweeps ALL of them, so a new one cannot reintroduce the class.

WHAT MUST KEEP WORKING, and why it is the load-bearing half of this file: a GENUINE provider throttle
still has to produce the helpful "retry shortly" notice. Otherwise this fix would have quietly deleted
a feature that exists for a good reason — a sender who is not told about a throttle assumes the
recipient ignored them.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

from service.api_core.authored_failures import (
    SERVICE_AUTHORED_FAILURE_REASONS,
    TURN_ENDED_WITHOUT_REPLY,
    is_service_authored,
)
from service.api_core.dispatch_text import (
    _auto_handoff_body_for_run,
    _is_provider_rate_limit_error,
)

REPO = Path(__file__).resolve().parents[2]

#: The verbatim shape of a real provider throttle, as it arrives in `error_text`.
REAL_PROVIDER_THROTTLE = (
    "API error 429: {\"type\":\"error\",\"error\":{\"type\":\"rate_limit_error\","
    "\"message\":\"Number of requests has exceeded your rate limit\"}}"
)

#: The cause that actually occurred and that the original list did not name.
REAL_PROVIDER_REFUSAL = (
    "stream error: the model declined to continue this turn (safety/policy refusal)"
)


def _row(**kw) -> dict:
    base = {
        "status": "failed",
        "from_agent": "graph-tech-lead",
        "target_agent": "graph-senior-dev-hermes",
        "error_text": "",
        "summary": "",
        "id": "run-x",
        "subject": "s",
    }
    base.update(kw)
    return base


class AuthoredFailureTextIsNotProviderEvidence(unittest.TestCase):
    # ── the incident, pinned exactly ──────────────────────────────────────────────────────────

    def test_the_reconcile_reason_does_NOT_produce_a_throttle_assertion(self):
        """THE REGRESSION TEST FOR THE REPORTED BUG. This is the precise input that reached a human as
        a false certainty."""
        body = _auto_handoff_body_for_run(
            _row(error_text=TURN_ENDED_WITHOUT_REPLY, summary=TURN_ENDED_WITHOUT_REPLY)
        )
        lowered = body.lower()
        for forbidden in ("is rate-limiting", "rate-limiting", "not your request", "retry shortly"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(
                    forbidden, lowered,
                    f"the notification still says {forbidden!r} for a run whose cause the record only "
                    "PRESUMES. Reported 2026-08-18: this assertion propagated through a tech lead "
                    "into a human-facing report and was wrong — the real cause was a provider safety "
                    f"refusal. Body: {body!r}",
                )

    def test_the_classifier_STILL_matches_the_token_so_the_guard_is_what_saves_it(self):
        """Proves the fix is where I claim it is. If the predicate had merely been reworded to stop
        matching, the test above would pass for the wrong reason and every other service-authored
        string would still be exposed."""
        self.assertTrue(
            _is_provider_rate_limit_error(TURN_ENDED_WITHOUT_REPLY),
            "the throttle predicate no longer matches the reconcile reason. If that was done by "
            "removing '429' from the reason text, the CLASS is still open: the next service-authored "
            "string mentioning a status code will be classified as a confirmed throttle.",
        )
        self.assertTrue(
            is_service_authored(TURN_ENDED_WITHOUT_REPLY),
            "the reconcile reason is not recognised as service-authored, so nothing stops the "
            "classifier from treating our own speculation as provider evidence.",
        )

    # ── the feature this must not have broken ─────────────────────────────────────────────────

    def test_a_REAL_provider_throttle_still_gets_the_retry_notice(self):
        """ANTI-VACUITY, and the load-bearing half: a fix that silences the throttle notice entirely
        would pass every assertion above while removing a feature that exists because a sender who is
        not told about a throttle concludes the recipient ignored them."""
        body = _auto_handoff_body_for_run(_row(error_text=REAL_PROVIDER_THROTTLE))
        self.assertIn("rate-limiting", body.lower(),
                      "a genuine provider 429 no longer produces the throttle notice")
        self.assertIn("retry shortly", body.lower(),
                      "a genuine provider 429 no longer tells the sender to retry")

    def test_a_real_provider_throttle_is_not_mistaken_for_service_text(self):
        self.assertFalse(
            is_service_authored(REAL_PROVIDER_THROTTLE),
            "provider error text was classified as service-authored, which would suppress the notice "
            "for exactly the case it exists for.",
        )

    # ── the branch that was missing ───────────────────────────────────────────────────────────

    def test_the_reason_enumerates_a_PROVIDER_REFUSAL(self):
        """The branch that actually occurred, and the only one where "retry shortly" is actively
        harmful: a retry re-triggers the refusal, spends quota, and delays the human intervention that
        is the only fix."""
        lowered = TURN_ENDED_WITHOUT_REPLY.lower()
        self.assertTrue(
            "refusal" in lowered or "declined" in lowered,
            "the reconcile reason still does not name a provider safety/policy refusal among the "
            f"possible causes. That is the branch the 2026-08-18 incident turned out to be: {TURN_ENDED_WITHOUT_REPLY!r}",
        )

    def test_the_reason_states_that_the_cause_is_UNDETERMINED(self):
        """So a reader who sees only this string is not misled, even if every consumer downstream is
        later rewritten by somebody who never read the module it lives in."""
        self.assertIn(
            "not determined", TURN_ENDED_WITHOUT_REPLY.lower(),
            "the reason no longer says the cause is undetermined. It lists several possibilities; a "
            "list of possibilities presented without that qualifier is what a downstream layer "
            "resolved into a single confident answer last time.",
        )

    def test_the_reason_does_not_assert_a_single_cause(self):
        for asserted in ("is rate-limiting", "presumed dead"):
            with self.subTest(asserted=asserted):
                self.assertNotIn(asserted, TURN_ENDED_WITHOUT_REPLY.lower())

    # ── the class, swept ──────────────────────────────────────────────────────────────────────

    def test_NO_service_authored_reason_is_treated_as_a_determined_provider_cause(self):
        """The registry exists so this can be checked for all of them at once. A future reason that
        happens to mention a status code must not reopen the class."""
        for reason in SERVICE_AUTHORED_FAILURE_REASONS:
            with self.subTest(reason=reason[:60]):
                body = _auto_handoff_body_for_run(_row(error_text=reason, summary=reason))
                self.assertNotIn(
                    "not your request", body.lower(),
                    "a service-authored failure reason produced a provider-throttle assertion. Add it "
                    "to SERVICE_AUTHORED_FAILURE_REASONS, or stop authoring it here.",
                )

    def test_the_reconciler_does_not_carry_its_own_COPY_of_the_reason(self):
        """One source. Two copies is how the writer and the consumer that must recognise it drift, and
        `is_service_authored` compares against the constant — a divergent copy silently stops being
        recognised, which restores the original bug with no test failing."""
        source = (REPO / "service" / "reconcilers" / "dispatch_lifecycle.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "TURN_ENDED_WITHOUT_REPLY", source,
            "dispatch_lifecycle no longer imports the shared reason constant",
        )
        # THE SHAPE OF THE ASSIGNMENT, checked with the AST rather than by searching for the old text.
        # Two earlier versions of this assertion were wrong in the same way: "presumed dead" appears in
        # the module docstring, and `(model 429, mid-turn` appears in the comment that RECORDS the
        # incident — so a text search forbade explaining the bug. A test that cannot coexist with its
        # own subject being documented is a test that will be deleted.
        #
        # What actually matters is that `reason` is bound to the shared NAME, never to a string
        # literal: `is_service_authored` compares against the constant, so a local copy silently stops
        # being recognised and the classifier reads "429" out of it again — the original bug, with
        # nothing failing.
        import ast

        tree = ast.parse(source)
        func = next(
            (n for n in ast.walk(tree)
             if isinstance(n, ast.AsyncFunctionDef) and n.name == "_fail_stranded_delivered_reply_runs"),
            None,
        )
        self.assertIsNotNone(func, "the stranded-reply reaper was renamed; this gate lost its subject")
        literal_reasons = [
            node for node in ast.walk(func)
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "reason" for t in node.targets)
            and isinstance(node.value, ast.Constant)
        ]
        self.assertEqual(
            literal_reasons, [],
            "the reaper assigns `reason` a string literal again instead of the shared "
            "TURN_ENDED_WITHOUT_REPLY constant. A local copy is not recognised by "
            "is_service_authored(), so the throttle classifier will read a status code out of it and "
            "report a guess as a determined cause — exactly the 2026-08-18 incident.",
        )

    def test_every_service_authored_reason_is_recognised_by_the_predicate(self):
        """The registry and the predicate must agree. A reason listed but not matched is a reason the
        guard does not actually cover."""
        for reason in SERVICE_AUTHORED_FAILURE_REASONS:
            with self.subTest(reason=reason[:60]):
                self.assertTrue(is_service_authored(reason))

    def test_a_clipped_or_wrapped_reason_is_still_recognised(self):
        """The notification path prefixes an intro and consumers clip for display. Recognition that
        only works on the untouched string is recognition that fails exactly when it matters."""
        clipped = TURN_ENDED_WITHOUT_REPLY[:120]
        wrapped = f"run run-abc: {TURN_ENDED_WITHOUT_REPLY} (reconcile)"
        for label, text in (("clipped", clipped), ("wrapped", wrapped)):
            with self.subTest(shape=label):
                self.assertTrue(
                    is_service_authored(text),
                    f"a {label} service-authored reason was not recognised: {text!r}",
                )

    def test_unrelated_text_is_not_swept_up(self):
        """The provenance check must not be so loose that it suppresses the notice for anything vague.
        Empty, unrelated, and genuine-provider text are all NOT ours."""
        for text in ("", "   ", "Run failed.", REAL_PROVIDER_REFUSAL, REAL_PROVIDER_THROTTLE):
            with self.subTest(text=text[:40]):
                self.assertFalse(is_service_authored(text))

    def test_the_word_boundary_in_the_classifier_is_intact(self):
        """Re-pinned because this fix touches the same predicate's call site: the boundary is what
        stops a token count or an exit code from reading as a throttle. It was deliberate."""
        self.assertFalse(_is_provider_rate_limit_error("exited with code 4290"),
                         "the boundary no longer excludes digit-adjacent runs")
        self.assertTrue(_is_provider_rate_limit_error("429 Too Many Requests"))
        self.assertTrue(_is_provider_rate_limit_error("code 429"))
        self.assertTrue(bool(re.search(r"\b429\b", "code 429")))

        # MEASURED, and it contradicts what the product comment used to claim. The comment said a
        # token count like "529 tokens" was excluded by the word boundary. It is not — a space IS a
        # word boundary, so this matches. Pinned as the TRUE behaviour rather than the intended one:
        # the comment described an intention the code never implemented, and the next reader would
        # otherwise inherit a guarantee that does not exist. The comment is corrected in the product;
        # this assertion is what keeps the two honest with each other.
        self.assertTrue(
            _is_provider_rate_limit_error("529 tokens generated"),
            "if this is now False the predicate was narrowed. That may be an improvement, but it is a "
            "BEHAVIOUR change to provider-text classification and needs its own reasoning — the "
            "2026-08-18 incident was about this predicate's INPUT, not its pattern.",
        )


if __name__ == "__main__":
    unittest.main()
