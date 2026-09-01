"""Failure text aify-comms wrote about itself. Never evidence about a provider.

THE INCIDENT (2026-08-18, reported by graph-tech-lead, confirmed). A sender was told, in the present
indicative:

    "graph-senior-dev-hermes couldn't respond — its model provider is rate-limiting / at a usage
     limit right now (a provider-side throttle, not your request). Please retry shortly."

The run record said something else entirely — that the cause was PRESUMED, and offered three
alternatives. The real cause was a fourth thing nobody had enumerated: the provider refused the turn
on safety grounds. The wrong cause propagated through a tech lead into a human-facing status report.

NOTHING CHOSE THAT WORDING FOR THAT RUN. `_is_provider_rate_limit_error` in `dispatch_text.py` ends
with `re.search(r"\\b(429|529)\\b", text)`, and the reconciler's own reason string contains the literal
"model 429" — as one item in a list of GUESSES. So the classifier matched our own speculation and
reported it as a determined fact.

The word boundary in that regex was added deliberately, so `529 tokens` and `code 4290` would not
false-positive. It was written carefully, for the right input: PROVIDER error text. It was then handed
service-authored prose, and no amount of care inside a predicate protects it from being asked the
wrong question. Rewording the reconciler would have fixed the one incident and left the mechanism
armed for the next string that happens to mention a status code.

WHAT THIS MODULE IS. The registry of reason strings the SERVICE writes when it fails a run on its own
initiative — no provider said any of it. Consumers that classify failure text ask
`is_service_authored()` first and skip provider inference when the answer is yes, because a cause we
invented cannot be evidence for a cause we are trying to infer.

WHY IDENTITY AND NOT KEYWORDS. The check compares against the actual constants, which each writer
imports from here. It does not try to detect hedging language ("presumed", "possible") — that would be
a second fragile predicate on top of the first, and the failure mode would again be silent. A string
is service-authored because it IS one of ours, which is a fact about provenance, not about wording.

A measured note on scope: at the time of writing, exactly ONE live service-authored reason was
misclassified by the throttle predicate (`dispatch_lifecycle`'s presumed-dead reason). It was enough
to reach a human as a false certainty.
"""

from __future__ import annotations

#: Failed by the reconcile sweep because a `require_reply` run's turn ended with no reply.
#:
#: THE CAUSE IS UNDETERMINED AND THIS STRING SAYS SO. It used to read "presumed dead (model 429,
#: mid-turn interrupt, or stall)" — a disjunction of three, stated as a parenthetical, which a
#: downstream classifier resolved into one. The rewrite is not cosmetic:
#:
#:   * it does not lead with a status code, because the code was never observed here;
#:   * it enumerates a PROVIDER REFUSAL, the branch the original list missed and the only one where
#:     "retry shortly" makes things actively worse — it re-triggers the refusal, spends quota, and
#:     delays the human intervention that is the only fix;
#:   * it says "not determined" in words, so a reader who sees only this string is not misled even if
#:     every consumer downstream is rewritten by someone who never read this file.
#:
#: 429 still appears, because naming the branch is useful and hiding it would be a different kind of
#: dishonesty. Safety comes from `is_service_authored()`, not from avoiding the token.
#: AND IT SAYS WHOSE FAILURE IT IS, because a reader supplied that themselves and got it wrong.
#:
#: Reported 2026-08-27. An operator read this line and concluded the agent had died. It had not: the
#: worker was mid-way through a large rebuild and still going. They sent two dispatches asking where
#: it had got to, interrupting exactly the work that needed uninterrupted attention, and the evidence
#: pointing the other way -- commits further along than the agent's last report -- had been sitting in
#: front of them the whole time and read as "commits I cannot account for".
#:
#: Nothing in the sentence was false. It described a RUN and never said so, and "the worker turn did
#: not finish" invites precisely one inference about the worker. This module already refuses to state
#: a cause it cannot determine; the same rule applies to the SUBJECT. A failed run is dispatch
#: bookkeeping, and liveness is a different question with a cheaper answer.
TURN_ENDED_WITHOUT_REPLY = (
    "Turn ended without a reply — this RUN was closed, which is NOT the same as the agent stopping: "
    "the worker may still be alive and working. Cause NOT DETERMINED; possible: a model-provider "
    "throttle (429/529), a provider safety or policy refusal, a mid-turn interrupt, or a stall. "
    "Failed by reconcile so the run isn't stranded as 'delivered'. Before treating the agent as dead, "
    "read its console (comms_console_tail) or comms_agent_info — this line is bookkeeping about one "
    "run, not evidence about the process."
)

#: Failed because somebody INTERRUPTED the turn, and we know because we recorded doing it.
#:
#: The undetermined string above lists "a mid-turn interrupt" among four possibilities -- and for an
#: interrupt this service issued itself, seconds earlier, guessing is inexcusable. `dispatch_controls`
#: holds the action, the requester and the run it belongs to. A cause we can look up must never be
#: reported as a cause we could not determine.
#:
#: THIS SENTENCE NAMED `terminal_controls` UNTIL 2026-08-25, AND THAT WAS WRONG. Nothing writes an
#: interrupt row there: measured twice on the live database, that table held 10 rows and then 29,
#: every one of them action 'start'. The first version of the reconciler's attribution believed this
#: comment, queried terminal_controls, and could therefore never fire once -- six green tests and a
#: feature that had never run. The comment was corrected in the reconciler and its tests the same
#: day and left standing here, which is its own lesson: a correction that does not grep leaves the
#: false copy in the file a reader is most likely to open next.
#:
#: Observed 2026-08-25: a run was interrupted through comms_interrupt and the failure that followed
#: still read "Cause NOT DETERMINED", inviting a reader to suspect a provider throttle or a policy
#: refusal for something an operator had just done on purpose.
def turn_interrupted(requested_by: str, at: str) -> str:
    who = str(requested_by or "").strip() or "an operator"
    when = str(at or "").strip()
    return (
        f"Turn was INTERRUPTED by {who}"
        + (f" at {when}" if when else "")
        + ". The run is failed because the turn did not reach a reply, not because anything went "
        "wrong upstream — there is no throttle, refusal or stall to investigate."
    )


#: Recognised after wrapping or truncation, same as the fragment below it.
_INTERRUPTED_FRAGMENT = "Turn was INTERRUPTED by"


#: Every reason in this tuple was written by aify-comms about its own reconciliation. Add a string here
#: when the SERVICE authors a run failure; do not add text a runtime or provider produced, because the
#: whole point of the registry is to tell those two apart.
SERVICE_AUTHORED_FAILURE_REASONS: tuple[str, ...] = (
    TURN_ENDED_WITHOUT_REPLY,
)

#: Enough of a reason to recognise it after a caller has wrapped or truncated it. Matching on a
#: distinctive fragment rather than the whole string keeps the check working when a consumer prefixes
#: a run id or clips for display — both of which happen on the notification path.
_FRAGMENTS: tuple[str, ...] = (
    "Failed by reconcile so the run isn't stranded",
    _INTERRUPTED_FRAGMENT,
)


def is_service_authored(text: str) -> bool:
    """Did aify-comms write this failure text about itself?

    True means: do NOT infer a provider-side cause from it. The text is our own account of what we
    observed (a turn that produced no reply), including any hypotheses we listed — and a hypothesis is
    not an observation, however confidently a later layer restates it.
    """
    candidate = str(text or "").strip()
    if not candidate:
        return False
    for reason in SERVICE_AUTHORED_FAILURE_REASONS:
        if reason in candidate or candidate in reason:
            return True
    return any(fragment in candidate for fragment in _FRAGMENTS)
