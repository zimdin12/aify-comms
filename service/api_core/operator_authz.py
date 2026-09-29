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

WHAT THIS MODULE DOES. With an operator key configured, operator privilege requires the
`X-Aify-Operator-Key` header to match it. The actor STRING still names who acted, for the audit trail;
it does not by itself grant anything when the gate is on.

WHAT IT HONESTLY DOES NOT DO, stated here so nobody reads more into it than it earns. On a host where
agents can read `.env`, or fetch the dashboard page that carries the key, a determined agent can still
obtain it. This raises the bar from "guess an English word" to "hold a secret", which stops the casual
and the prompt-injected case; it is NOT a security boundary against an agent with filesystem access.
The real boundary is authenticating the service itself (`API_KEY` unset here) and giving the dashboard
its own credential — an operator decision, recorded in the v0.6 plan, not something a helper can fix.

OFF UNLESS CONFIGURED (v0.7.5, operator ruling: "it should be off by default. api key is that trust
surface, external ones are basically blocked from doing serious actions"). With no `OPERATOR_KEY` in
`.env` there is no operator gate: an operator claim that reached these endpoints was already let in by
the API key, and an external key never reaches them (`external_keys.py` admits it to sending only). So
the claim is granted, and on a host with no API key the whole port is the trusted LAN the README
describes. Setting `OPERATOR_KEY` turns the gate on, and then the claim needs the header as above. The
key was generated automatically before this, which made the gate the default on every host.

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

#: What the dashboard sends on every request when the service has no operator key, so a send it makes AS
#: an agent is still told apart from that agent (see `operator_is_acting`). A claim, not a credential:
#: with the gate off, the API key is the trust boundary, and a caller that sends it only stops a send it
#: makes from counting as the named agent being present.
OPERATOR_SURFACE_HEADER = "X-Aify-Operator"

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
        return False  # nothing to prove against; `authorize_operator` decides what an unset key means
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
    -- this function grants the OVERRIDE, it does not replace the owner comparison. With no operator key
    configured the gate is off and an operator claim is granted; with one, the claim must present it.
    """
    if not is_operator_actor(actor):
        return False
    if not str(configured_key or ""):
        return True
    if operator_privilege_granted(request, configured_key):
        return True
    raise HTTPException(
        403,
        f"'{actor}' claims operator privilege for {action} without a valid "
        f"{OPERATOR_KEY_HEADER} header. The actor name records WHO acted; it does not grant "
        f"permission.",
    )

def operator_is_acting(request) -> bool:
    """Does this request PROVE it comes from an operator surface, whatever actor it names?

    The dashboard can send AS any agent from its identity picker, and such a send is the operator,
    not the agent: it must not count as the agent being present (see `_touch_agent`). With an operator
    key configured the dashboard proves it with that key, which no bridge holds; without one it says so
    with `OPERATOR_SURFACE_HEADER`.
    """
    configured = operator_key_from(request)
    if configured:
        return operator_privilege_granted(request, configured)
    try:
        return str(request.headers.get(OPERATOR_SURFACE_HEADER) or "").strip().lower() == "dashboard"
    except Exception:
        return False


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


def refuse_an_unproven_operator_claim(actor: str, request, *, action: str) -> None:
    """Refuse (403) a request that names the operator without proving it, when the gate is on.

    For every route where naming `dashboard` or `operator` grants something beyond a label: a message
    read as the operator's (the sending routes), a start that REPLACES a live instance
    (`start_intent_for_requester`), a spawn brief delivered as the dashboard's, a steer or interrupt an
    agent is told came from the operator, a compaction by an unregistered caller. Pass the actor the
    route will RECORD, after its own default, so an omitted name that becomes `dashboard` is gated too
    (review of 0.7.6, S4). With no `OPERATOR_KEY` it grants, as `authorize_operator` does.
    """
    authorize_operator(actor, request, operator_key_from(request), action=action)


#: The name a route records for a caller that gave none, on the routes only the dashboard calls unnamed.
DASHBOARD_ACTOR = "dashboard"


def recorded_operator_actor(name, request, *, action: str) -> str:
    """The actor a route records: the caller's name, or `dashboard` when it gave none. Refused (403) when
    that is an operator name the request cannot prove.

    For the routes that attribute an omitted name to the dashboard. The bridge and aify-env name
    themselves on every one of them, so an omitted name is the dashboard's or a raw HTTP caller's, and
    with `OPERATOR_KEY` set it must prove itself like an explicit `dashboard`. A route whose ordinary
    callers do omit the name (`comms_run_interrupt`, hermes' session-handle) gates only an explicit
    claim, with `refuse_an_unproven_operator_claim`.
    """
    actor = str(name or "").strip() or DASHBOARD_ACTOR
    refuse_an_unproven_operator_claim(actor, request, action=action)
    return actor


def refuse_an_unproven_operator_sender(sender: str, request) -> None:
    """A message may name the operator as its sender only as far as `authorize_operator` allows.

    A message from `dashboard` is read as the operator's own: the managed wake prompt leaves the peer
    trust rule out for it, and the Claude channel tells a session that only other senders are agents.
    So the sending routes gate the sender name exactly as the destructive endpoints gate the actor:
    granted with no `OPERATOR_KEY` (the API key is the boundary), and with one set, refused (403)
    unless the request presents it. Before this the key gated nothing on a send (review of 0.7.6, O3).
    """
    refuse_an_unproven_operator_claim(sender, request, action="sending a message as the operator")
