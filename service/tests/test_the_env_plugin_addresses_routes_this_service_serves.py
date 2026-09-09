"""Every request aify-env's aify-comms plugin sends is a route this service actually serves.

THE SEAM THIS COVERS IS THE ONE THAT IS LOAD-BEARING NOW. v0.6.2 deleted the environment-bridge tier
and, with it, five of the six tests that drove a real aify-env. `docs/V063_ACCEPTANCE_LEDGER.md`
records what that left: each side is exercised in its own suite and the JOIN between them is proved
once, by `the-credential-ref-we-write-is-one-aify-env-resolves.test.js`. The join that replaced the
deleted one is aify-env's plugin talking to this service's HTTP API, and nothing asserted it.

WHAT IT ASSERTS, and it is narrow on purpose. Every `#send(METHOD, path)` in the plugin names a
route this app serves, at that method. That is agreement about the ADDRESS, which is the half that
fails silently: a renamed path answers 404 and the plugin's caller reports a failed claim, a missing
terminal, an unstarted spawn -- the shapes `docs/PHASE8_STATUS.md` records as sitting on the joins
between components that each reported healthy. It says nothing about request bodies, response
shapes, or auth, and a passing run must not be read as saying it does.

IT DRIVES NO DAEMON. Starting aify-env supersedes the one serving this host and reaps its workers,
so this reads the plugin's SOURCE and the app's own route table. Point `AIFY_ENV_REPO` at any
checkout to judge that one instead.

IT SKIPS BY NAME rather than passing when the checkout is absent. A cross-repo proof that quietly
ran nothing is the failure CLAUDE.md records at length.
"""

from __future__ import annotations

import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: Where the sibling repo lives. `AIFY_ENV_REPO` is what lets this be driven against a MUTATED
#: copy without touching the operator's checkout.
DEFAULT_ENV_REPO = Path.home() / "projects" / "aify-env"

PLUGIN_DIR = Path("lib") / "plugins" / "aify-comms"

#: `this.#send("POST", "/spawn-requests/claim", ...)` and its template-literal form. The method is a
#: plain string at every call site; the path is a string or a backtick template.
SEND_CALL = re.compile(
    r"#send\(\s*[\"']([A-Z]+)[\"']\s*,\s*(?:[\"']([^\"']+)[\"']|`([^`]+)`)")

#: The prefix `#send` puts in front of every path it is given.
API_PREFIX = "/api/v1"

#: An interpolation is a value, so it stands for whatever a path parameter stands for.
INTERPOLATION = re.compile(r"\$\{[^}]*\}")


def env_repo() -> tuple[Path | None, str]:
    """The checkout to judge, or None and the reason nothing was compared.

    AN EXPLICIT `AIFY_ENV_REPO` IS THE ONLY CANDIDATE. It used to be the FIRST of two, falling back
    to the default checkout when it held no plugin -- so a run pointed at one tree silently judged a
    different one. That is not a hypothetical: the mutation run that found it set the variable to an
    empty directory to exercise the skip, and the gate answered GREEN because it had quietly gone and
    asked the real question instead. A verdict has to belong to the thing it was aimed at.
    """
    named = os.environ.get("AIFY_ENV_REPO", "").strip()
    if named:
        if (Path(named) / PLUGIN_DIR).is_dir():
            return Path(named), ''
        return None, (f'AIFY_ENV_REPO names {named}, which holds no {PLUGIN_DIR.as_posix()} -- refusing '
                      'to judge a different checkout than the one asked for')
    if (DEFAULT_ENV_REPO / PLUGIN_DIR).is_dir():
        return DEFAULT_ENV_REPO, ''
    return None, (f'no aify-env checkout at {DEFAULT_ENV_REPO} and AIFY_ENV_REPO is unset')


def plugin_requests(repo: Path) -> list[tuple[str, str, str]]:
    """(method, normalised path, source file) for every request the plugin sends."""
    found: list[tuple[str, str, str]] = []
    for source in sorted((repo / PLUGIN_DIR).glob("*.mjs")):
        text = source.read_text(encoding="utf-8", errors="replace")
        for method, plain, template in SEND_CALL.findall(text):
            raw = plain or template
            # A QUERY STRING IS NOT PART OF THE ADDRESS. `/sessions?agentId=...` is the `/sessions`
            # route; keeping the query would make every parameterised read look like a missing one.
            path = INTERPOLATION.sub("{}", raw.split("?", 1)[0])
            found.append((method, API_PREFIX + path, source.name))
    return found


def served_routes() -> set[tuple[str, str]]:
    """(method, path template) for every HTTP route this app serves."""
    from service.main import create_app

    app = create_app()
    out: set[tuple[str, str]] = set()
    for route in app.routes:
        path = getattr(route, "path", None)
        if not path:
            continue
        for method in sorted(getattr(route, "methods", None) or []):
            if method in {"HEAD", "OPTIONS"}:
                continue
            out.add((method, path))
    return out


def _matches(request_path: str, route_path: str) -> bool:
    """Does this address name that route? A `{param}` segment stands for one value."""
    request = request_path.strip("/").split("/")
    route = route_path.strip("/").split("/")
    if len(request) != len(route):
        return False
    for asked, served in zip(request, route):
        if served.startswith("{") and served.endswith("}"):
            continue
        if asked == "{}":
            # An interpolated value where the route wants a LITERAL is a different address.
            return False
        if asked != served:
            return False
    return True


def unserved(requests, routes) -> list[str]:
    wrong = []
    for method, path, source in requests:
        paths = {route for m, route in routes if m == method}
        if any(_matches(path, route) for route in paths):
            continue
        elsewhere = sorted(m for m, route in routes if _matches(path, route))
        detail = (f"served only for {elsewhere}" if elsewhere else "no route of any method matches")
        wrong.append(f"{source} sends {method} {path} -- {detail}")
    return wrong


class TheEnvPluginAddressesRoutesThisServiceServes(unittest.TestCase):
    def setUp(self) -> None:
        self.repo, reason = env_repo()
        if self.repo is None:
            self.skipTest(
                f"{reason}, so the plugin's addresses were NOT compared against this service's "
                "routes")
        self.requests = plugin_requests(self.repo)
        self.routes = served_routes()

    def test_the_scan_found_both_sides(self) -> None:
        """The control. An empty request list or an empty route table satisfies the gate below."""
        self.assertGreaterEqual(
            len(self.requests), 8,
            f"only {len(self.requests)} request(s) found in {self.repo / PLUGIN_DIR} -- the plugin "
            "moved, or the call shape changed and this reads a surface that no longer exists")
        self.assertGreaterEqual(
            len(self.routes), 50,
            "implausibly few routes on the app, so a match below would mean nothing")

    def test_the_matcher_can_say_no(self) -> None:
        """Negative control, on the matcher itself: it must refuse addresses it should refuse."""
        self.assertTrue(_matches("/api/v1/terminals/{}/launch", "/api/v1/terminals/{tid}/launch"))
        self.assertFalse(_matches("/api/v1/terminals/{}/launch", "/api/v1/terminals/{tid}/output"))
        self.assertFalse(_matches("/api/v1/terminals/{}", "/api/v1/terminals/{tid}/launch"))
        # A VALUE WHERE THE ROUTE WANTS A LITERAL is a different address: `/controls/{id}` must not
        # be read as `/controls/claim`, which is a fixed endpoint and not a parameter.
        self.assertFalse(_matches("/api/v1/terminals/controls/{}", "/api/v1/terminals/controls/claim"))

    def test_the_check_would_report_an_address_this_service_does_not_serve(self) -> None:
        """Positive control on the CHECK, driven by feeding it a request that must be reported."""
        invented = [("GET", "/api/v1/not-a-route-this-service-serves", "carrier.mjs")]
        self.assertEqual(len(unserved(invented, self.routes)), 1)
        self.assertEqual(unserved(self.requests[:1], self.routes), [],
                         "the first real request must still pass while the carrier fails, or this "
                         "control is reporting something other than the address")

    def test_every_request_the_plugin_sends_is_a_route_this_service_serves(self) -> None:
        wrong = unserved(self.requests, self.routes)
        self.assertEqual(wrong, [], (
            "aify-env's aify-comms plugin addresses this service at a route it does not serve, "
            "which answers 404 and surfaces as a failed claim or a missing terminal rather than as "
            "a routing error:\n  " + "\n  ".join(wrong)))


if __name__ == "__main__":
    unittest.main()
