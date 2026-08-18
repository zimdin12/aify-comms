"""Operator privilege must be PROVEN, not asserted. One authority, three destructive endpoints.

THE REGRESSION THIS CLOSES (reported 2026-08-18 as R5-H1, HIGH, confirmed). The ownership checks added
to unsend, channel-delete and artifact-unshare each exempted an actor named `dashboard` or `operator`:

    if actor not in _UNSEND_OPERATOR_ACTORS and actor != author:
        raise HTTPException(403, ...)

`actor` is a request PARAMETER the caller chooses. So any caller could pass `requestedBy="operator"`
and delete any message, delete any channel, or unshare any artifact — with no knowledge of the victim
and no credential — and the audit trail would then record "operator" as the one who did it. The fix for
a casual ownership hole opened a universal one, and it framed the operator while doing it.

MEASURED ON THIS DEPLOYMENT BEFORE WRITING THE FIX, because the severity depends on what else guards
the endpoint, and the answer was: nothing.

  * Every bridge sends the SAME shared `X-API-Key` (`mcp/stdio/aify-service-endpoint.mjs`), so that key
    proves "inside the trust boundary", never "I am the dashboard".
  * `api_key` is not configured at all here — the middleware is only installed `if config.api_key` — and
    `cors_origins` is `*`. So those three endpoints were reachable unauthenticated by anything that
    could open a socket to the port, gated by a guessable English word.

WHAT THIS MODULE DOES. Operator privilege now requires the `X-Aify-Operator-Key` header to match a
configured secret. The actor STRING still names who acted, for the audit trail; it no longer grants
anything. Fails closed in both directions: an unconfigured key means no caller can claim operator
privilege at all, because a privilege with no credential behind it is exactly the bug being fixed.

WHAT IT HONESTLY DOES NOT DO, stated here so nobody reads more into it than it earns. On a host where
agents can read `.env`, or fetch the dashboard page that carries the key, a determined agent can still
obtain it. This raises the bar from "guess an English word" to "hold a secret", which stops the casual
and the prompt-injected case; it is NOT a security boundary against an agent with filesystem access.
The real boundary is authenticating the service itself (`API_KEY` unset here) and giving the dashboard
its own credential — an operator decision, recorded in the v0.6 plan, not something a helper can fix.

ONE AUTHORITY, not three copies. The three call sites each had their own frozenset with the same two
strings — the forked-constant shape this repo keeps removing, and the reason a fix applied to one site
would have left the other two open.
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException

#: The header a dashboard/operator surface presents to prove it may act on another agent's behalf.
#: Deliberately NOT the same header as the service API key: every bridge holds that one.
OPERATOR_KEY_HEADER = "X-Aify-Operator-Key"

#: Actor strings that REQUEST operator privilege. Naming one is a claim, not a grant — the claim is
#: verified against the header below. Kept as one set because three copies is how two of them get fixed.
OPERATOR_ACTORS = frozenset({"dashboard", "operator"})


def is_operator_actor(actor: str) -> bool:
    """Does this actor string claim operator privilege? Says nothing about whether it HAS it."""
    return str(actor or "").strip().lower() in OPERATOR_ACTORS


def operator_privilege_granted(request, configured_key: str) -> bool:
    """Compare the presented header against the configured operator key, in constant time.

    `hmac.compare_digest` rather than `==` for the same reason the API-key middleware uses it: an
    early-exit comparison on a secret leaks its prefix to a caller who can time the response.
    """
    configured = str(configured_key or "")
    if not configured:
        return False  # nothing to prove against — see the fail-closed note in `authorize_operator`
    presented = ""
    try:
        presented = str(request.headers.get(OPERATOR_KEY_HEADER) or "")
    except Exception:
        presented = ""
    if not presented:
        return False
    return hmac.compare_digest(presented.encode("utf-8", "ignore"),
                               configured.encode("utf-8", "ignore"))


def authorize_operator(actor: str, request, configured_key: str, *, action: str) -> bool:
    """Return True if `actor` may act on another agent's behalf. Raise 403 if it claimed and failed.

    Returns False for an ordinary agent actor, which leaves the caller's own ownership check to decide
    — this function grants the OVERRIDE, it does not replace the owner comparison.

    FAILS CLOSED WHEN THE KEY IS UNCONFIGURED, and that is the deliberate half. The alternative —
    "no key configured, so allow the operator strings" — is precisely the vulnerability, restored by
    default, on every deployment that never sets one. A privilege whose credential is absent is not a
    privilege; the error says so and names the fix, because a 403 an operator cannot explain is how a
    security fix gets reverted.
    """
    if not is_operator_actor(actor):
        return False
    if operator_privilege_granted(request, configured_key):
        return True
    if not str(configured_key or ""):
        raise HTTPException(
            403,
            f"'{actor}' claims operator privilege for {action}, but no operator key is configured on "
            f"this service, so the claim cannot be verified. Set OPERATOR_KEY in .env and restart the "
            f"service; the dashboard sends it automatically. Until then only an item's own owner may "
            f"act on it. (This used to be granted on the actor string alone, which let any caller "
            f"delete anything by naming itself 'operator'.)",
        )
    raise HTTPException(
        403,
        f"'{actor}' claims operator privilege for {action} without a valid "
        f"{OPERATOR_KEY_HEADER} header. The actor name records WHO acted; it does not grant "
        f"permission.",
    )

def operator_key_from(request) -> str:
    """The configured operator secret for this app, or "" when unset (which refuses every claim).

    Lives HERE rather than in each router. It was copied into all three call sites first, which
    `test_no_forked_declarations` caught — the same fork the shared vocabulary was created to end, and
    a helper that reads a security setting is the last place to want three copies.
    """
    try:
        return str(getattr(request.app.state.config, "operator_key", "") or "")
    except Exception:
        return ""
