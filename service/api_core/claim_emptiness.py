""""Is there still nothing to do?" — the five predicates that decide whether a long-poll keeps waiting.

PURE. No database, no router, no imports from this service: five dict reads and nothing else.

WHAT THEY DECIDE. `longpoll()` runs a claim attempt, and if the result is EMPTY it holds the request
open and retries until work arrives or the client's wait elapses. These predicates are the only
definition of "empty" the service has, and each of the five claim endpoints has its own, because
each returns a differently-shaped result.

WHY THEY LIVE HERE. Until v0.5.4 all five were written INLINE at the `longpoll()` call — four
one-line lambdas and one nested `def`. None could be imported, so none could be tested, and the
nested `def` was never entered by the suite at all: `longpoll` reads `if wait_ms <= 0 or not
is_empty(result)`, and `or` short-circuits, so a claim with the default `waitMs=0` never calls the
predicate. Every test the service had used that default.

BOTH FAILURE MODES ARE SILENT, which is why five one-line functions are worth their own module:

* Too EAGER (says empty when the result is actionable) — the endpoint holds the request open with a
  claimed run in hand. The bridge sees a claim that timed out; the run sits claimed by nobody.
* Too RELUCTANT (says non-empty when there is nothing) — `longpoll` returns on the first attempt
  every time. The long-poll silently degrades to the short-poll it replaced, at whatever rate the
  bridge polls, and the only symptom is request volume.

NOTE HOW LITTLE THEY AGREE. Three different ways of asking "is this result well-formed and empty":
`spawn_request` requires its key to be PRESENT, `environment_control` requires a companion key to be
ABSENT, and the two control-list predicates compare for EXACT `[]` — so a result with no `controls`
key at all is treated as actionable rather than empty. They are transcribed here as they were, with
each asymmetry tested; making them agree is a behaviour change and a reviewer's call, not a move.
"""

from __future__ import annotations


def dispatch_claim_is_empty(result: dict) -> bool:
    """`/dispatch/claim`. Keep waiting ONLY for a pure "nothing to do" result.

    Any actionable signal — a claimed run, or a `stopped` / `release` / `blockedBy` directive —
    returns immediately. The three directives matter as much as the run does: `stopped` and
    `release` tell a bridge to tear a worker down, and holding those for the length of a long poll
    delays a shutdown the operator already asked for.
    """
    return (
        result.get("run") is None
        and not result.get("stopped")
        and not result.get("release")
        and not result.get("blockedBy")
    )


def dispatch_controls_is_empty(result: dict) -> bool:
    """`/dispatch/controls/claim`. EXACT `[]`, not falsiness.

    A result with no `controls` key answers False and returns at once. That is deliberate as
    written: an unrecognised result shape is not evidence that there is nothing to do.
    """
    return result.get("controls") == []


def terminal_controls_is_empty(result: dict) -> bool:
    """`/terminals/controls/claim`. Same rule as the dispatch controls list.

    Kept as its own name rather than shared: the two endpoints claim different tables through
    different handlers, and one changing shape must not silently redefine emptiness for the other.
    The pair is asserted to agree in `test_claim_emptiness.py`, so a divergence is a decision.
    """
    return result.get("controls") == []


def environment_control_is_empty(result: dict) -> bool:
    """`/environments/controls/claim`. The companion key is a NEGATIVE guard.

    `controlId` present means the handler is reporting on a specific control, so the request has its
    answer even when `control` itself is None — that is a real result, not an empty poll.
    """
    return result.get("control") is None and "controlId" not in result


def spawn_request_is_empty(result: dict) -> bool:
    """`/spawn-requests/claim`. The companion key is a POSITIVE guard — the opposite shape to the
    environment predicate above.

    `spawnRequest` must be PRESENT and None. A result that never mentioned it is some other shape
    (an error body, a future field set), and waiting on one would hold the request open for a
    handler that has already answered.
    """
    return (
        result.get("spawnRequest") is None
        and not result.get("blockedBy")
        and "spawnRequest" in result
    )
