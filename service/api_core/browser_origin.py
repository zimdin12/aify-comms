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
    *, method: str = "GET", sec_fetch_site: str = "", origin: str = "",
    host: str = "", allowed_origins=None,
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

    if origin:
        if origin.lower() in _named_origins(allowed_origins):
            return True
        origin_host = _hostname_of(origin)
        return bool(origin_host) and origin_host == _hostname_of(host)

    if site == "cross-site":
        return False
    if site == "same-site" and str(method or "GET").strip().upper() not in SAFE_METHODS:
        return False
    return True
