"""With `OPERATOR_KEY` set, NO route accepts the name `dashboard` or `operator` without the key.

THE CLASS, found one route at a time (external review of 0.7.6, S4, then the dev review of its fix): the
key gated the routes someone remembered, and each round found more that recorded `dashboard` or acted
on it without proof -- stop-worker, session control, session-mode, then terminal stop, environment
admin, console start, rename. A list of gated routes goes stale in the direction that opens a hole.

SO THE POPULATION IS DERIVED from the app's own route table: every mutating route whose request names
an actor (a body field, a query parameter, or a key its handler reads out of a raw JSON body). Each is
sent `dashboard`, with the key configured and not presented, and must answer 403 before it looks
anything up: the probes target records that do not exist, so any other gate order answers 404.

A SECOND RULE keeps the omitted-name case derived too: no router spells its own `or "dashboard"`
default. A route that records an unnamed caller as the dashboard does it through
`recorded_operator_actor`, which gates the name it returns.
"""

from __future__ import annotations

import inspect
import re
import typing
from pathlib import Path

from service.tests._base import FastApiTestCase
from service.tests.served_routes import walk_routes

ROUTERS = Path(__file__).resolve().parents[1] / "routers"
SECRET = "s3cret-operator-key"
#: What only the operator gate says (`authorize_operator`). A route's own 403 (not a channel member,
#: say) is a different refusal, so the gate is recognised by this, not by the status.
GATE_SAYS = "does not grant permission"
#: A field that names who is acting, BY SHAPE, not by list: the list missed `handledBy` on control
#: settlement (dev review of 0.7.6). camelCase `...By`, or a `from` / `actor` spelling.
ACTOR_SHAPE = re.compile(r"^(?:[a-z]+By|from|from_|from_agent|fromAgent|actor)$")
#: Fields of that shape that name something other than the requester, each with why.
NOT_AN_ACTOR = {
    "managedBy": "registration: the agent that manages the one being registered, read by no privilege check",
}
#: A raw-body handler reading an actor-shaped key: `body.get("requestedBy")` and the like.
RAW_ACTOR_READ = re.compile(r"""\.get\(\s*["']([a-z]+By|from|from_agent|fromAgent|actor)["']""")
#: A raw-body handler that delegates the actor read: `require_operator(body, ...)` reads `requestedBy`.
DELEGATED_ACTOR_READ = re.compile(r"\brequire_operator\(\s*body\b")


def is_actor_field(key: str) -> bool:
    return bool(ACTOR_SHAPE.match(key)) and key not in NOT_AN_ACTOR


def _dummy(annotation):
    """A value that satisfies a required field's type, so validation lets the request reach the handler."""
    origin = typing.get_origin(annotation)
    if origin is typing.Union:
        options = [a for a in typing.get_args(annotation) if a is not type(None)]
        return _dummy(options[0]) if options else None
    if origin in (list, tuple, set) or annotation in (list, tuple, set):
        return []
    if origin is dict or annotation is dict:
        return {}
    if annotation is bool:
        return False
    if annotation in (int, float):
        return 0
    return "x"


def _probes(app):
    """(method, path, where, field, required_body) for every mutating route that names an actor."""
    found = []
    for route in walk_routes(app):
        methods = sorted((getattr(route, "methods", None) or set()) - {"GET", "HEAD"})
        if not methods:
            continue
        dependant = route.dependant
        body, actor = {}, None
        for param in dependant.body_params:
            for name, field in (getattr(param.field_info.annotation, "model_fields", {}) or {}).items():
                key = field.alias or name
                if is_actor_field(key) and actor is None:
                    actor = ("body", key)
                elif field.is_required():
                    body[key] = _dummy(field.annotation)
        for query in dependant.query_params:
            if is_actor_field(query.alias) and actor is None:
                actor = ("query", query.alias)
        if actor is None and not dependant.body_params:
            source = inspect.getsource(route.endpoint)
            read = RAW_ACTOR_READ.search(source)
            if read and is_actor_field(read.group(1)):
                actor = ("body", read.group(1))
            elif DELEGATED_ACTOR_READ.search(source):
                # The handler hands its body to `require_operator`, which reads `requestedBy` there, out
                # of this source's sight: the release and reset routes went unprobed that way.
                actor = ("body", "requestedBy")
        if actor is None:
            continue
        path = re.sub(r"\{[^}]+\}", "no-such-record", route.path)
        found.append((methods[0], path, actor[0], actor[1], body))
    return found


class NoRouteAcceptsAnUnprovenOperatorName(FastApiTestCase):
    DB_NAME = "aify-no-unproven-operator-name.db"

    def _send(self, method, path, where, field, body, actor, headers):
        if where == "query":
            return self.client.request(method, path, params={field: actor}, headers=headers)
        return self.client.request(method, path, json={**body, field: actor}, headers=headers)

    def test_CONTROL_the_walk_finds_the_actor_routes_it_must(self):
        """The census is only as good as the walk: these are routes known to name an actor, one per carrier."""
        probes = {(m, p): (w, f) for m, p, w, f, _ in _probes(self.client.app)}
        self.assertEqual(probes.get(("POST", "/api/v1/messages/send")), ("body", "from_agent"))
        self.assertEqual(probes.get(("DELETE", "/api/v1/messages/no-such-record")), ("query", "requestedBy"))
        self.assertEqual(probes.get(("POST", "/api/v1/agents/no-such-record/stop-worker")), ("body", "requestedBy"),
                         "the raw-body reader missed stop-worker, which reads requestedBy by hand")
        self.assertEqual(probes.get(("PATCH", "/api/v1/dispatch/controls/no-such-record")), ("body", "handledBy"),
                         "the shape rule missed handledBy, the field a hand-written list missed")
        self.assertEqual(probes.get(("POST", "/api/v1/agent-definitions/no-such-record/release")), ("body", "requestedBy"),
                         "the walk missed a handler that hands its body to require_operator")
        self.assertGreaterEqual(len(probes), 25, f"the walk found only {len(probes)} actor routes")

    def test_with_the_key_set_every_route_refuses_an_unproven_dashboard(self):
        self.client.app.state.config.operator_key = SECRET
        for method, path, where, field, body in _probes(self.client.app):
            with self.subTest(route=f"{method} {path}", field=field):
                response = self._send(method, path, where, field, body, "dashboard", {})
                self.assertEqual(response.status_code, 403, response.text[:200])
                self.assertIn(GATE_SAYS, response.text, "refused, but not by the operator gate")

    def test_CONTROL_with_no_key_configured_no_route_refuses_the_name(self):
        """ANTI-VACUITY: a route that refused everything, or failed validation, would pass the test above."""
        self.client.app.state.config.operator_key = ""
        for method, path, where, field, body in _probes(self.client.app):
            with self.subTest(route=f"{method} {path}", field=field):
                response = self._send(method, path, where, field, body, "dashboard", {})
                self.assertNotEqual(response.status_code, 422, f"the probe never reached the handler: {response.text[:200]}")
                self.assertNotIn(GATE_SAYS, response.text, "the gate refused with no key configured")

    def test_no_router_spells_its_own_dashboard_default(self):
        spelled = re.compile(r"""or\s+["']dashboard["']""")
        offenders = [f"{path.relative_to(ROUTERS)}:{n}"
                     for path in sorted(ROUTERS.rglob("*.py"))
                     for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                     if spelled.search(line)]
        self.assertEqual(offenders, [], "record an unnamed caller through recorded_operator_actor, which gates it")

    def test_CONTROL_the_actor_shape_says_yes_and_no(self):
        for key in ("requestedBy", "handledBy", "createdBy", "from", "from_agent", "fromAgent"):
            self.assertTrue(is_actor_field(key), key)
        for key in ("managedBy", "agentId", "to", "body", "machineId", "status"):
            self.assertFalse(is_actor_field(key), key)

    def test_every_exemption_still_names_a_field_some_request_carries(self):
        """A stale exemption would silently cover a future field of the same name."""
        import service.models as models
        carried = {f.alias or n for cls in vars(models).values()
                   if isinstance(cls, type) and hasattr(cls, "model_fields")
                   for n, f in cls.model_fields.items()}
        for key in NOT_AN_ACTOR:
            self.assertIn(key, carried, f"{key} is exempt but no request model carries it any more")

    def test_CONTROL_the_default_census_sees_a_default(self):
        self.assertTrue(re.search(r"""or\s+["']dashboard["']""", 'x = name or "dashboard"'))
