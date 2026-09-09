"""Every request aify-env's aify-comms plugin EMITS is a route this service serves.

THE SEAM THIS COVERS IS THE ONE THAT IS LOAD-BEARING NOW. v0.6.2 deleted the environment-bridge tier
and, with it, five of the six tests that drove a real aify-env. `docs/V063_ACCEPTANCE_LEDGER.md`
records what that left: each side is exercised in its own suite and the JOIN between them is proved
once, by `the-credential-ref-we-write-is-one-aify-env-resolves.test.js`, which establishes credential
grammar and directory agreement rather than lifecycle integration. The join that replaced the deleted
one is aify-env's plugin talking to this service's HTTP API, and nothing asserted it.

IT READS THE EMITTED URL, NOT THE SOURCE, and the first version's failure is the reason. That one
matched `#send("METHOD", path)` with a regex and prepended `/api/v1` itself, and review broke it
twice: legal whitespace turning `this.#send(` into `this.#send (` dropped one of the ten requests
with every assertion still passing, and changing the plugin's ACTUAL prefix to something this service
does not serve left it green, because the gate was supplying the prefix rather than reading it. A
parse of a call site is not the address that goes out.

So every public method of `CommsApi` is now CALLED, with the `fetchImpl` injection the class already
takes for exactly this, and the URL the transport receives is what gets judged. The method list comes
from the prototype, not from a list here: a new method that reaches an unserved route is a failure
rather than an omission.

WHAT IT ASSERTS, and it is narrow on purpose. The ADDRESS and the METHOD -- the half that fails
silently, since a renamed path answers 404 and the plugin's caller reports a failed claim, a missing
terminal or an unstarted spawn rather than a routing error. It says nothing about request bodies,
response shapes or auth, and a passing run must not be read as saying it does.

IT DRIVES NO DAEMON AND REACHES NO NETWORK. `CommsApi` is a client class; the injected fetch records
and answers. Starting aify-env supersedes the one serving this host, so nothing here imports its
entrypoint. Point `AIFY_ENV_REPO` at any checkout to judge that one.

IT SKIPS BY NAME rather than passing when the checkout is absent. A cross-repo proof that quietly ran
nothing is the failure CLAUDE.md records at length.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: Where the sibling repo lives. `AIFY_ENV_REPO` is what lets this be driven against a MUTATED copy
#: without touching the operator's checkout.
DEFAULT_ENV_REPO = Path.home() / "projects" / "aify-env"

PLUGIN_DIR = Path("lib") / "plugins" / "aify-comms"

#: A value distinctive enough that it cannot be mistaken for a literal path segment. Every path
#: parameter is filled with it, so a concrete address is judged rather than a template.
PROBE = "PROBE-VALUE"

#: THE PROBE IS AN OBJECT THAT PRINTS AS A STRING, and both halves are needed. Several of these
#: methods SPREAD their argument into the request body -- `heartbeat`, `terminalOutput`, `report` --
#: and a string spread into an object becomes `{0: "P", 1: "R", ...}`: the first version passed a
#: string and the field check reported nine undeclared NUMERIC keys, a harness artefact wearing a
#: defect's clothes. Others coerce it into a path segment or an action. An object whose `toString`
#: is non-enumerable spreads to nothing and still prints as the probe, so one value serves both.

#: Node, driving the real class with the injection it already exposes. `identity` and `credential`
#: are synthetic; the fetch records and answers. The method list is read off the PROTOTYPE so a new
#: request cannot be added without this gate seeing it.
#:
#: EVERY REQUEST CARRIES ITS OWNER. The recorder stamps whichever method is running, because a
#: TOTAL is not a relation: a silent method beside one that sends twice satisfies
#: `len(requests) == len(methods)` exactly. Review built that pair.
HARNESS = """
import { CommsApi, mintBridgeIdentity } from '%(api)s';
const seen = [];
let running = '(none)';
const api = new CommsApi({
  endpoint: 'http://probe.invalid:1',
  credential: async () => '',
  identity: mintBridgeIdentity({ version: '0.0.0-probe' }),
  fetchImpl: async (url, init) => {
    seen.push({ owner: running, url: String(url), method: String((init && init.method) || 'GET'),
                body: (init && init.body) ? String(init.body) : '' });
    return { ok: true, status: 200, json: async () => ({}), text: async () => '{}' };
  },
});
// SPREADS TO NOTHING, PRINTS AS THE PROBE -- see the note beside PROBE in the Python module.
const PROBE = Object.defineProperty({}, 'toString',
  { value: () => '%(probe)s', enumerable: false });
const names = Object.getOwnPropertyNames(Object.getPrototypeOf(api))
  .filter((n) => n !== 'constructor' && !n.startsWith('_'))
  .filter((n) => {
    const d = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(api), n);
    return d && typeof d.value === 'function';
  });
const failed = [];
for (const name of names) {
  running = name;
  try { await api[name](PROBE, PROBE); }
  catch (error) { failed.push(name + ': ' + String(error && error.message)); }
  running = '(between calls)';
}
console.log(JSON.stringify({ methods: names, requests: seen, failed }));
"""


def env_repo() -> tuple[Path | None, str]:
    """The checkout to judge, or None and the reason nothing was compared.

    AN EXPLICIT `AIFY_ENV_REPO` IS THE ONLY CANDIDATE. It used to be the FIRST of two, falling back
    to the default checkout when the named one held no plugin -- so a run pointed at one tree silently
    judged a different one. The mutation run that found it set the variable to an empty directory to
    exercise the skip, and the gate answered GREEN because it had quietly gone and asked the real
    question instead. A verdict has to belong to the thing it was aimed at.
    """
    named = os.environ.get("AIFY_ENV_REPO", "").strip()
    if named:
        if (Path(named) / PLUGIN_DIR).is_dir():
            return Path(named), ""
        return None, (f"AIFY_ENV_REPO names {named}, which holds no {PLUGIN_DIR.as_posix()} -- "
                      "refusing to judge a different checkout than the one asked for")
    if (DEFAULT_ENV_REPO / PLUGIN_DIR).is_dir():
        return DEFAULT_ENV_REPO, ""
    return None, f"no aify-env checkout at {DEFAULT_ENV_REPO} and AIFY_ENV_REPO is unset"


def emitted_requests(repo: Path) -> dict:
    """Call every public method of the real `CommsApi` and report the URLs its transport saw.

    The harness is written to a scratch file and the import is an ABSOLUTE file URL, because a
    relative specifier resolves against the SCRIPT's directory and not the working directory -- a
    `-e` script run with `cwd=repo` looked for the plugin beside itself and found nothing.
    """
    api = (repo / PLUGIN_DIR / "api.mjs").as_uri()
    script = Path(tempfile.mkdtemp()) / "drive-the-plugin.mjs"
    script.write_text(HARNESS % {"probe": PROBE, "api": api}, encoding="utf-8")
    result = subprocess.run(
        ["node", str(script)], cwd=repo, capture_output=True, text=True)
    # A PAYLOAD FROM A PROCESS THAT DID NOT SUCCEED IS NOT EVIDENCE. This read stdout and
    # ignored the exit status, so a plugin copy setting `process.exitCode = 7` while printing
    # normal JSON was admitted and every assertion passed on it.
    if result.returncode != 0:
        raise AssertionError(
            f"the driver exited {result.returncode}, so nothing it printed is admitted: "
            f"{result.stdout[-400:]}{result.stderr[-400:]}")
    payload = [line for line in result.stdout.splitlines() if line.startswith("{")]
    if not payload:
        raise AssertionError(
            "the plugin's own client could not be driven, so this gate judged nothing: "
            f"{result.stdout[-400:]}{result.stderr[-400:]}")
    return json.loads(payload[-1])


def declared_body_fields() -> dict[tuple[str, str], set[str]]:
    """(method, path) -> the TOP-LEVEL field names that route's request model declares.

    PYDANTIC IGNORES WHAT IT DOES NOT DECLARE, which is why this is worth asserting: a renamed or
    mistyped key is not an error anywhere. The request succeeds, the value never arrives, and the
    only symptom is behaviour that quietly stops happening.

    Aliases count as declared names, because an alias is what the wire is allowed to say.
    """
    from service.main import create_app

    app = create_app()
    out: dict[tuple[str, str], set[str]] = {}
    for route in app.routes:
        path = getattr(route, "path", None)
        body = getattr(route, "body_field", None)
        model = getattr(getattr(body, "field_info", None), "annotation", None)
        fields = getattr(model, "model_fields", None)
        if not path or not fields:
            continue
        names: set[str] = set()
        for name, field in fields.items():
            names.add(name)
            alias = getattr(field, "alias", None)
            if alias:
                names.add(str(alias))
            validation = getattr(field, "validation_alias", None)
            if isinstance(validation, str):
                names.add(validation)
        for method in sorted(getattr(route, "methods", None) or []):
            if method in {"HEAD", "OPTIONS"}:
                continue
            out[(method, path)] = names
    return out


def undeclared_fields(requests: list[dict], declared: dict[tuple[str, str], set[str]],
                      routes: set[tuple[str, str]]) -> list[str]:
    """Field names the plugin sends that the matching route does not declare."""
    wrong = []
    for request in requests:
        if not request.get("body"):
            continue
        try:
            sent = json.loads(request["body"])
        except (TypeError, ValueError):
            continue
        if not isinstance(sent, dict):
            continue
        path, method = _path_of(request["url"]), request["method"].upper()
        for (m, route), names in declared.items():
            if m != method or not _matches(path, route):
                continue
            for key in sorted(set(sent) - names):
                wrong.append(f"{method} {path} sends `{key}`, which {route}'s model does not "
                             f"declare -- Pydantic drops it silently")
            break
        else:
            wrong.append(f"{method} {path} carries a body and matched no route with a model")
    return wrong


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


def _path_of(url: str) -> str:
    """The path an emitted URL addresses, without scheme, host or query."""
    without_scheme = url.split("://", 1)[-1]
    path = "/" + without_scheme.partition("/")[2]
    return path.split("?", 1)[0].split("#", 1)[0]


def _matches(request_path: str, route_path: str) -> bool:
    """Does this address name that route? A `{param}` segment stands for one value."""
    request = request_path.strip("/").split("/")
    route = route_path.strip("/").split("/")
    if len(request) != len(route):
        return False
    for asked, served in zip(request, route):
        if served.startswith("{") and served.endswith("}"):
            continue
        if asked != served:
            return False
    return True


def unserved(requests: list[dict], routes: set[tuple[str, str]]) -> list[str]:
    wrong = []
    for request in requests:
        path = _path_of(request["url"])
        method = request["method"].upper()
        if any(_matches(path, route) for m, route in routes if m == method):
            continue
        elsewhere = sorted({m for m, route in routes if _matches(path, route)})
        detail = f"served only for {elsewhere}" if elsewhere else "no route of any method matches"
        wrong.append(f"{method} {path} -- {detail}")
    return wrong


class TheEnvPluginAddressesRoutesThisServiceServes(unittest.TestCase):
    def setUp(self) -> None:
        self.repo, reason = env_repo()
        if self.repo is None:
            self.skipTest(
                f"{reason}, so the plugin's addresses were NOT compared against this service's "
                "routes")
        self.driven = emitted_requests(self.repo)
        self.routes = served_routes()
        self.declared = declared_body_fields()

    def test_every_public_method_emitted_a_request(self) -> None:
        """EACH METHOD emits exactly one request, which is the relation the claim needs.

        A TOTAL IS NOT A RELATION. `len(requests) == len(methods)` is satisfied by a method that
        emits nothing paired with one that emits twice, and review built exactly that: a silent
        `agents()` beside a `heartbeat()` sending an extra valid GET, ten methods, ten requests,
        every assertion green. Before that it was a FLOOR -- at least eight -- which nine
        satisfied when there were ten. Ownership is stamped on each request by the recorder.
        """
        methods = self.driven["methods"]
        self.assertGreaterEqual(
            len(methods), 8,
            f"only {len(methods)} public method(s) on CommsApi -- the class moved, or the harness "
            "read something that is not it")
        self.assertEqual(
            self.driven["failed"], [],
            "a public method raised before reaching the transport, so its address went unjudged")
        per_method = {name: 0 for name in methods}
        for request in self.driven["requests"]:
            per_method[request["owner"]] = per_method.get(request["owner"], 0) + 1
        self.assertEqual(
            {name: count for name, count in per_method.items() if count != 1}, {},
            "every public method must emit exactly one request: a method that emits none leaves "
            "its address unchecked, and one that emits two hides that gap in the total")

    def test_the_service_offers_a_surface_to_compare_against(self) -> None:
        """The other half of the control: an empty route table satisfies every match below."""
        self.assertGreaterEqual(
            len(self.routes), 50,
            "implausibly few routes on the app, so a match below would mean nothing")

    def test_the_matcher_can_say_no(self) -> None:
        """Negative control on the matcher itself, and on the URL reader beside it."""
        self.assertTrue(_matches("/api/v1/terminals/X/launch", "/api/v1/terminals/{tid}/launch"))
        self.assertFalse(_matches("/api/v1/terminals/X/launch", "/api/v1/terminals/{tid}/output"))
        self.assertFalse(_matches("/api/v1/terminals/X", "/api/v1/terminals/{tid}/launch"))
        self.assertFalse(_matches("/api/v1/agents", "/api/v2/agents"))
        self.assertEqual(_path_of("http://h:1/api/v1/sessions?agentId=a"), "/api/v1/sessions")
        self.assertEqual(_path_of("http://h:1/api/review-wrong/agents"), "/api/review-wrong/agents")

    def test_the_check_would_report_an_address_this_service_does_not_serve(self) -> None:
        """Positive control on the CHECK, driven by feeding it a request that must be reported."""
        invented = [{"url": "http://h:1/api/v1/not-a-route-this-service-serves", "method": "GET"}]
        self.assertEqual(len(unserved(invented, self.routes)), 1)
        self.assertEqual(
            unserved(self.driven["requests"][:1], self.routes), [],
            "the first real request must still pass while the carrier fails, or this control is "
            "reporting something other than the address")

    def test_the_field_scan_found_bodies_and_models(self) -> None:
        """The control. No bodies, or no models, and the assertion below is satisfied by nothing."""
        with_bodies = [r for r in self.driven["requests"] if r.get("body")]
        self.assertGreaterEqual(
            len(with_bodies), 4,
            f"only {len(with_bodies)} emitted request(s) carried a body, so the field check "
            "judged almost nothing")
        self.assertGreaterEqual(
            len(self.declared), 20,
            "implausibly few routes declare a request model, so a match below would mean nothing")

    def test_the_field_check_reports_a_name_the_model_does_not_declare(self) -> None:
        """Positive control on the CHECK, driven by a field this service certainly does not have."""
        invented = [{
            "url": "http://h:1/api/v1/environments/heartbeat", "method": "POST",
            "body": json.dumps({"not_a_field_this_service_declares": 1}),
        }]
        self.assertEqual(len(undeclared_fields(invented, self.declared, self.routes)), 1)

    def test_every_field_the_plugin_sends_is_one_the_route_declares(self) -> None:
        """TOP-LEVEL fields only, and a nested object's keys are not judged here.

        Pydantic ignores an undeclared field, so a renamed key is not an error anywhere: the
        request succeeds, the value never arrives, and the symptom is behaviour that quietly
        stops happening. `only_if_no_live_session` is a live instance -- spell it wrong on the
        wire and every other test still passes while the race it closes reopens.
        """
        wrong = undeclared_fields(self.driven["requests"], self.declared, self.routes)
        self.assertEqual(wrong, [], (
            "aify-env's plugin sends a field this service does not declare, which Pydantic drops "
            "in silence:" + chr(10) + "  " + (chr(10) + "  ").join(wrong)))

    def test_every_request_the_plugin_emits_is_a_route_this_service_serves(self) -> None:
        wrong = unserved(self.driven["requests"], self.routes)
        self.assertEqual(wrong, [], (
            "aify-env's aify-comms plugin addresses this service at a route it does not serve, "
            "which answers 404 and surfaces as a failed claim or a missing terminal rather than as "
            "a routing error:\n  " + "\n  ".join(wrong)))


if __name__ == "__main__":
    unittest.main()
