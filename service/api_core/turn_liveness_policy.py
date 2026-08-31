"""ONE answer to "is this turn still live", for the two readers that must not disagree.

DELIVERY AND STATUS ARE THE SAME QUESTION and were being answered by two bodies of code with
different rules. Delivery (`claim_gating._turn_busy_holds_delivery`) implements the operator's
"trust only verifiable renewals" ruling: a turn whose bridge row is independently observable may
renew up to `TURN_LEASE_ABSOLUTE_MAX_SECONDS` (4 hours), while anything unverifiable is cut at the
strict 30-minute anchor. The status clamp cut everything at 30 minutes.

So a genuinely working agent with a live, verifiable bridge kept its queued work for up to four
hours -- correctly -- while the dashboard said it had stopped working after thirty minutes. Two
answers to one question, and the operator sees the wrong one.

THIS IS A PURE FUNCTION AND IT TAKES THE VERDICT, NOT THE DATABASE. Whether a lease is renewable is
an ownership question about `bridge_instances` and belongs to the caller that can ask it; what to do
with that answer is policy, and policy in two places is how the two drifted in the first place.

The parameters are passed rather than imported so a test can drive the boundaries directly, and so
the module has no dependency on either caller.
"""

from __future__ import annotations


def turn_is_still_live(
    *,
    started_epoch: float,
    touched_epoch: float,
    renewable: bool,
    now_epoch: float,
    strict_seconds: float,
    absolute_max_seconds: float,
) -> bool:
    """Should this turn still count as running?

    `started_epoch` is when the turn BEGAN and must not move while it runs -- that is the whole
    reason the anchor columns exist. `touched_epoch` is when something last said it was still going,
    which a timer-driven poster refreshes and which therefore cannot bound anything on its own.

    A VERIFIED CLAIM ages against the last renewal, bounded absolutely from the start. Nothing
    checkable ages against the start alone, because re-stamps prove nothing when no independent
    observer is backing them.

    A FUTURE TIMESTAMP MUST NOT HOLD. `now - seen` goes negative for a clock-skewed or bad write,
    which trivially satisfies `<= ceiling`, so the turn would be live for ever -- the exact permanent
    strand the ceiling exists to bound. Requiring a non-negative age closes it.

    NO ANCHOR AT ALL IS NOT A LIVE TURN. Every writer stamps a timestamp, so a blank or unparseable
    pair is a corrupt row rather than a running turn. Both callers prefer the recoverable failure:
    one message delivered mid-turn, or one status reading `available` a little early, beats an agent
    that never receives work again or shows `working` for ever.
    """
    if renewable:
        if started_epoch and (now_epoch - started_epoch) > absolute_max_seconds:
            return False
        # A FUTURE last-touch is not a renewal. Without this, verifying a lease could make a turn
        # LESS live than not verifying it -- a clock-skewed or bad `touched` write gives a negative
        # age, which the caller below rejects, while the unverified path would have aged the start
        # anchor and said live. That inversion matters beyond tidiness: the status readers check the
        # strict bound FIRST and only pay for the ownership query when it says no, which is exact
        # only while verification can add liveness and never remove it.
        usable_touch = touched_epoch if (touched_epoch and touched_epoch <= now_epoch) else 0.0
        # THE MORE RECENT OF THE TWO. A renewal cannot precede the turn it renews, so a `touched`
        # older than `started` is a corrupt row rather than a stale renewal -- and taking it would
        # make a turn that began ten seconds ago look half an hour idle.
        seen = max(usable_touch, started_epoch)
    else:
        # The start anchor is the bound. The last-touch column is a fallback ONLY for rows written
        # before the anchor existed; the boot backfill fills those, so it is transitional.
        seen = started_epoch or touched_epoch
    if not seen:
        return False
    age = now_epoch - seen
    return 0 <= age <= strict_seconds
