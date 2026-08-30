"""May a BROWSER drive this service — one decision, consulted by HTTP and by the WebSocket.

THE THREAT, recorded in KNOWN_ISSUES since the 2026-06-28 audit: with CORS `*` and no key, a page
the operator merely visits can drive every mutating endpoint — including `POST
/agents/{id}/console/input`, which types into a live PTY — and read every response. Binding loopback
does not help, because the browser is already on the machine.

WHY THE HTTP GUARD WAS NOT ENOUGH, and the reason this module exists rather than a second copy of
the rule. It refused only `Sec-Fetch-Site: cross-site`, which leaves two ways in:

  * A hostile `Origin` with NO Fetch Metadata header passed untouched. `Origin` is itself a browser
    signal and was never consulted, so the guard declined to look at the one header that names who
    is calling.
  * `same-site` passed WHOLESALE. Same-site means the registrable DOMAIN matches, not the host —
    an attacker-controlled sibling subdomain is same-site — so the weakest of the three values was
    treated as proof.

The WebSocket check already had the right model (compare by HOST, so the second dashboard on
another port still works) while HTTP had the weaker one. Two guards for one question, disagreeing,
is how the weaker one becomes the way in. This is the single policy both now ask.

PURE. Every input is handed in, so each combination can be driven without a socket, and the decision
cannot quietly become "whatever the last request did".
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: Methods that cannot change state, so a browser NAVIGATION to them is not an attack.
#:
#: This is what keeps the same-site rule from breaking the operator: clicking a link from Dashboard
#: Next on :8801 through to :8800 is a same-site navigation that carries no `Origin` at all, and
#: refusing it would break a real flow to stop nothing. An unsafe method from the same position is a
#: different matter, and is refused below.
#: Hosts this service will believe it is being reached on, when a BROWSER is asking.
#:
#: WHY SELF-AGREEMENT IS NOT EVIDENCE. Comparing the Origin's host to the `Host` header looks like a
#: same-origin check and is not one: both values come from the CLIENT. Under DNS rebinding an
#: attacker's page is served from `evil.example`, that name is re-resolved to this service, and the
#: browser then sends `Origin: http://evil.example` AND `Host: evil.example`. They match perfectly,
#: and the request is not same-origin at all. DNS rebinding is the threat the 2026-06-28 audit named
#: and the one this guard kept claiming to close.
#:
#: So the same-host shortcut only applies on a host we independently trust. Loopback is trusted
#: because a rebound name is never one of these; anything else the operator adds is a decision.
DEFAULT_TRUSTED_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

#: Paths a browser may NAVIGATE to from a same-site page without an accepted Origin.
#:
#: EXPLICIT, BECAUSE "GET IS SAFE" IS FALSE HERE. That assumption is the general rule and this
#: service breaks it: `GET /messages/inbox/{agent}` settles read receipts, completes dispatch runs
#: stranded by a dead bridge, and refreshes the caller's status -- unless `peek` is set. A blanket
#: same-site GET allowance therefore let a sibling subdomain MUTATE through the arm called safe.
#:
#: What remains is the surface a browser genuinely navigates to: the dashboard, the redirect that
#: reaches it, and the static/meta paths that carry no state. Everything else -- the whole API --
#: needs an accepted Origin, whatever its method.
NAVIGABLE_PATHS = (
    "/api/v1/dashboard",
    "/health", "/ready", "/version", "/docs", "/redoc", "/openapi.json", "/favicon",
)


def host_is_trusted(host: str, trusted_hosts=None) -> bool:
    """Is `Host` one this service accepts a same-host claim on?"""
    name = _hostname_of(host)
    if not name:
        return False
    named = {str(entry).strip().lower() for entry in (trusted_hosts or []) if str(entry).strip()}
    return name in (named or set()) or name in DEFAULT_TRUSTED_HOSTS


def path_is_navigable(path: str) -> bool:
    """A path a same-site navigation may reach without an accepted Origin."""
    text = str(path or "").strip()
    if text in ("", "/"):
        return True
    # EXACT, OR A REAL CHILD. The bare `startswith(p)` that used to be here matched far more than
    # the routes it named: `/health-evil`, `/docsanything` and `/api/v1/dashboard-evil` were all
    # navigable, so a same-site navigation reached any route whose path merely BEGAN with a safe
    # one. A path boundary is a `/`, not a character count.
    return any(text == p or text.startswith(p.rstrip("/") + "/") for p in NAVIGABLE_PATHS)


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _named_origins(allowed_origins) -> set[str]:
    """`cors_origins`, normalised. `*` grants NOTHING and that is deliberate.

    A wildcard is the absence of a decision about who may drive this service from a browser, not a
    decision to trust every page. Reading it as "everyone" would make the guard a no-op in exactly
    the default configuration it exists to protect.
    """
    return {
        str(entry).strip().rstrip("/").lower()
        for entry in (allowed_origins or [])
        if str(entry).strip() not in ("", "*")
    }


def _hostname_of(value: str) -> str:
    """The host NAME out of an origin or a `Host` header, parsed rather than split.

    PARSED, NOT SPLIT ON THE LAST COLON. That shortcut is right for `localhost:8800` and wrong for
    every IPv6 form: `[::1]` served on port 80 has no port to strip, and splitting on the last colon
    yields `":"` — which matches no origin, so a legitimate same-origin request is refused. The
    address is bracketed precisely so it can be told from a port, and `urlsplit` already knows how.
    """
    text = str(value or "").strip()
    if not text:
        return ""
    if "//" not in text:
        text = f"//{text}"
    return (urlsplit(text).hostname or "").lower()


def is_browser_navigation(sec_fetch_dest: str, method: str = "GET") -> bool:
    """Is this a browser loading a PAGE, as opposed to any other caller?

    DISTINGUISHED POSITIVELY, which matters because the answer decides whether to change a caller's
    contract. `Sec-Fetch-Dest: document` is set by browsers on a top-level navigation and by no
    program -- unlike `Sec-Fetch-Mode`, which Node's own `fetch` sends as `cors` and which cost this
    repo seven red tests when it was mistaken for a browser signal. Inferring "browser" from the
    ABSENCE of something would sweep in every program.
    """
    return (
        str(sec_fetch_dest or "").strip().lower() == "document"
        and str(method or "GET").strip().upper() in SAFE_METHODS
    )


def url_without_api_key(url: str) -> str:
    """The same URL with `api_key` removed and every other parameter kept.

    KEEPING THE REST IS THE POINT. Dropping the whole query would silently discard wherever the
    operator was navigating to -- a page, a filter -- and land them somewhere they did not ask for,
    which reads as the redirect being broken rather than as a credential being cleaned up.
    """
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    parts = urlsplit(str(url or ""))
    kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != "api_key"]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(kept), parts.fragment))


def browser_request_is_allowed(
    *, method: str = "GET", path: str = "/", sec_fetch_site: str = "", sec_fetch_dest: str = "",
    origin: str = "", host: str = "", allowed_origins=None, trusted_hosts=None,
) -> bool:
    """The whole decision, in the order the evidence deserves.

    AN ORIGIN IS THE STRONGEST SIGNAL, so it is read first and it is CONCLUSIVE. A browser attaches
    it on every cross-origin request and on every unsafe same-origin one, page script cannot forge
    it, and it names the caller. If one is present it must be a host we serve or one the operator
    named — and if it is neither, no Fetch Metadata value rescues it. That ordering is the fix: the
    old guard reached for `Sec-Fetch-Site` first and never looked at `Origin` at all.

    Compared by HOST, not by full origin, because the dashboard is a browser client too: the classic
    UI is same-origin and Dashboard Next answers on another PORT of the same host. Ports do not make
    a different site, and an attacker cannot serve from the operator's own hostname.

    WITH NO ORIGIN, Fetch Metadata is all there is. `cross-site` is refused outright. `same-site` is
    refused for unsafe methods only — it means the registrable domain matches, not the host, so a
    sibling subdomain qualifies; but a plain navigation from one is harmless and refusing it would
    break the operator's own link between dashboards.

    ABSENT EVERYTHING MEANS NOT A BROWSER — a bridge, a CLI, a test, `curl` — and those are the
    callers this service exists to serve. A browser cannot omit both, so this is not the hole it
    looks like; refusing on absence would refuse every legitimate client and protect nobody.
    """
    origin = str(origin or "").strip().rstrip("/")
    site = str(sec_fetch_site or "").strip().lower()
    dest = str(sec_fetch_dest or "").strip().lower()

    # A BROWSER MUST REACH US ON A TRUSTED HOST, whichever arm it would otherwise take.
    #
    # The Host check used to live INSIDE the Origin branch, and that left the rebinding fix
    # walk-aroundable by simply not sending the header it keyed on: under a rebind the browser
    # regards the rebound attacker name as SAME-ORIGIN -- it is the origin, once the name resolves
    # here -- and a GET may omit `Origin` entirely. Executed by review:
    #   GET /api/v1/messages/inbox/x, Sec-Fetch-Site: same-origin, no Origin,
    #   Host: evil.example (untrusted)  =>  ALLOWED.
    # And that GET settles read receipts and completes stranded dispatch runs.
    #
    # An operator-NAMED origin is still honoured below whatever the Host: that is an explicit
    # decision about a specific third party, not a shortcut inferred from self-agreement.
    browser_identified = bool(site) or bool(dest)
    if browser_identified and origin.lower() not in _named_origins(allowed_origins):
        if not host_is_trusted(host, trusted_hosts):
            return False

    if origin:
        # An origin the OPERATOR named is a decision, and it stands whatever the Host says.
        if origin.lower() in _named_origins(allowed_origins):
            return True
        # THE SAME-HOST SHORTCUT REQUIRES A TRUSTED HOST. Origin and Host both come from the client,
        # so their agreeing proves nothing on its own: under DNS rebinding both are the attacker's
        # name, re-resolved to this service. Requiring the Host to be independently trusted is what
        # actually closes it -- a rebound name is not loopback and is not in the operator's list.
        if not host_is_trusted(host, trusted_hosts):
            return False
        origin_host = _hostname_of(origin)
        return bool(origin_host) and origin_host == _hostname_of(host)

    if site == "cross-site":
        return False
    if site == "same-site":
        # NOT "any safe method". `GET /messages/inbox/{agent}` settles read receipts, completes
        # stranded dispatch runs and refreshes agent status, so a blanket same-site GET allowance
        # let a sibling subdomain mutate through the arm called safe. Only a positive top-level
        # NAVIGATION to the non-mutating surface is allowed; the API needs an accepted Origin.
        return (
            is_browser_navigation(sec_fetch_dest, method)
            and path_is_navigable(path)
        )
    return True
