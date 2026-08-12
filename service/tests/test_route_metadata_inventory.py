"""Route DECORATOR metadata is a second contract, and body-compare cannot see it.

THE v0.5.x ROUTE GATE, established BEFORE any route handler moves — same discipline as
`test_route_inventory.py` and `test_process_global_identity.py`, both of which were written before
the v0.5 extraction rather than after it.

`test_route_inventory.py` pins METHOD + PATH. That is necessary and too weak. A route handler carries
a second contract that lives entirely OUTSIDE its function body:

    @router.post("/x", response_model=Thing, status_code=201, tags=["api"], dependencies=[...])
    async def handler(...):   # <- AST body-compare only ever sees from here down

Moving 103 handlers onto per-domain `APIRouter`s is precisely where that second contract gets
dropped, and an AST body-compare passes green while the product breaks.

THE ONE THAT WOULD HAVE BEEN MISSED WITHOUT THIS GATE — `route_class`:

`api_v2.py` defines `JsonApiRoute(APIRoute)`, and all 103 handlers run on it. It is not decoration:
it wraps every handler in a bounded retry over SQLite write-lock contention, which is the fix for the
recurring `database is locked` 503s. `route_class` is configured on the ROUTER, not on the decorator,
so a handler moved onto a freshly-constructed `APIRouter()` silently degrades to a plain `APIRoute`
and loses lock-retry. Nothing in the body changed, the path and method are identical, every existing
test stays green — and the endpoint starts 503ing under concurrent writes. Load-dependent, so it
would surface in production and not in CI. That is the exact failure class this series has to make
impossible, so the route class is snapshotted per route.

WHAT IS DELIBERATELY *NOT* PINNED: the endpoint's module. `service.routers.api_v2.register_agent`
becoming `service.routers.agents.register_agent` is the POINT of the refactor. The function NAME is
pinned (a rename is an API-visible change to `operationId` generation and to every `url_for`); the
module is reported by `test_route_owner_map` below as a separate, deliberately-updated artifact so
that each slice's ownership change is a reviewable line in a diff rather than a side effect.

WHEN THIS FAILS AND THE CHANGE IS INTENTIONAL: update the snapshot in the SAME commit, and say in the
message which field changed and why it is inert. A snapshot regenerated without reading the diff is
worth nothing — see the false-green history in `no-evidence-is-not-a-pass`.
"""

from __future__ import annotations

import unittest
from pathlib import Path

SNAPSHOT = Path(__file__).resolve().parent / "data" / "route_metadata_inventory.txt"
OWNER_MAP = Path(__file__).resolve().parent / "data" / "route_owner_map.txt"


def _fmt(value) -> str:
    if value is None:
        return "-"
    if isinstance(value, (list, tuple, set)):
        return ",".join(sorted(str(v) for v in value)) or "-"
    return str(value)


def _live_metadata() -> list[str]:
    """One line per route, every decorator-carried field that can change behaviour."""
    from service.main import create_app

    app = create_app()
    rows = []
    for route in app.routes:
        path = getattr(route, "path", None)
        if path is None:
            continue
        methods = getattr(route, "methods", None)
        verbs = sorted(m for m in methods if m not in {"HEAD", "OPTIONS"}) if methods else ["WS"]
        endpoint = getattr(route, "endpoint", None)
        # response_model is resolved by FastAPI; compare by name so the line stays readable.
        model = getattr(route, "response_model", None)
        model_name = getattr(model, "__name__", None) or (_fmt(model) if model is not None else "-")
        for verb in verbs:
            rows.append(
                " ".join(
                    [
                        f"{type(route).__name__}",
                        f"{verb}",
                        f"{path}",
                        f"name={_fmt(getattr(route, 'name', None) or getattr(endpoint, '__name__', None))}",
                        f"status={_fmt(getattr(route, 'status_code', None))}",
                        f"response_model={model_name}",
                        f"tags={_fmt(getattr(route, 'tags', None))}",
                        f"deps={len(getattr(route, 'dependencies', None) or [])}",
                        f"in_schema={_fmt(getattr(route, 'include_in_schema', None))}",
                    ]
                )
            )
    return sorted(rows)


def _shadowing_pairs() -> list[str]:
    """Ordered pairs where an EARLIER path pattern would swallow a LATER literal path.

    Absolute route order is not pinned: splitting one router into twelve necessarily renumbers
    everything, and a gate that fails on every slice teaches people to regenerate it blindly. What
    actually breaks is narrower — FastAPI matches in registration order, so if `/agents/{agent_id}`
    is registered before `/agents/live`, the literal route becomes unreachable and `agent_id` quietly
    equals "live". Only those pairs are pinned, because only those pairs can change meaning.
    """
    import re

    from service.main import create_app

    app = create_app()
    seq = []
    for index, route in enumerate(app.routes):
        path = getattr(route, "path", None)
        if path is None:
            continue
        methods = getattr(route, "methods", None)
        verbs = frozenset(m for m in methods if m not in {"HEAD", "OPTIONS"}) if methods else frozenset({"WS"})
        seq.append((index, path, verbs))

    def to_regex(p: str) -> re.Pattern:
        return re.compile("^" + re.sub(r"\{[^}]+\}", r"[^/]+", re.escape(p).replace(r"\{", "{").replace(r"\}", "}")) + "$")

    pairs = []
    for i, (_, p_pat, v_pat) in enumerate(seq):
        if "{" not in p_pat:
            continue
        rx = to_regex(p_pat)
        for _, p_lit, v_lit in seq[i + 1:]:
            if "{" in p_lit or not (v_pat & v_lit):
                continue
            if rx.match(p_lit):
                pairs.append(f"{sorted(v_pat & v_lit)[0]} {p_pat} SHADOWS {p_lit}")
    return sorted(set(pairs))


class RouteMetadataInventoryTests(unittest.TestCase):
    def test_route_metadata_matches_the_snapshot(self):
        expected = [l for l in SNAPSHOT.read_text(encoding="utf-8").splitlines() if l.strip()]
        actual = _live_metadata()
        missing = [r for r in expected if r not in actual]
        added = [r for r in actual if r not in expected]
        self.assertEqual(
            (missing, added),
            ([], []),
            "Route decorator metadata changed.\n"
            f"  GONE:  {missing}\n"
            f"  NEW:   {added}\n"
            "A body-only AST compare CANNOT see this. If a route moved to a new module and this "
            "fired, the move dropped part of the decorator contract — most likely `route_class`, "
            "which carries the SQLite lock-retry. Fix the move, do not regenerate the snapshot.",
        )

    def test_every_db_writing_route_kept_its_lock_retry(self):
        """The one that survives a careless snapshot regeneration.

        Pinned as an INVARIANT rather than a count, so it still holds after the routes move and all
        the numbers change: a state-mutating route whose module talks to SQLite must be on
        JsonApiRoute, because that is what retries a transient write-lock instead of 503ing.

        Scoped to modules that actually import `service.db`. The first draft asserted over every
        mutating `/api/v1` route and immediately failed on the four container control endpoints —
        which touch Docker, never SQLite, and so can never hit the write lock. That was the
        assertion being too broad, not the code being wrong; widening a rule until it accuses
        innocent code is how a gate gets "fixed" by deleting it.
        """
        import sys

        from service.main import create_app

        app = create_app()
        offenders = []
        for route in app.routes:
            path = getattr(route, "path", "") or ""
            methods = getattr(route, "methods", None) or set()
            if not path.startswith("/api/v1") or not (methods & {"POST", "PATCH", "PUT", "DELETE"}):
                continue
            endpoint = getattr(route, "endpoint", None)
            module = sys.modules.get(getattr(endpoint, "__module__", "") or "")
            touches_db = module is not None and hasattr(module, "get_db")
            if touches_db and type(route).__name__ != "JsonApiRoute":
                offenders.append(f"{sorted(methods)} {path} is {type(route).__name__}")
        self.assertEqual(
            offenders,
            [],
            "State-mutating /api/v1 routes are NOT on JsonApiRoute, so they lost the bounded "
            "retry over SQLite write-lock contention and will 503 under concurrent writes:\n  "
            + "\n  ".join(offenders)
            + "\nCause is almost always a handler moved onto a bare APIRouter(); the router must be "
            "constructed with route_class=JsonApiRoute.",
        )

    def test_no_new_route_shadowing(self):
        """Registration order only matters where a pattern can swallow a literal path."""
        expected = [l for l in OWNER_MAP.read_text(encoding="utf-8").splitlines()
                    if l.strip() and l.startswith("SHADOW ")]
        actual = [f"SHADOW {p}" for p in _shadowing_pairs()]
        self.assertEqual(
            sorted(actual),
            sorted(expected),
            "Route shadowing changed. A pattern route registered before a literal route makes the "
            "literal unreachable — the path parameter silently captures the literal segment. This "
            "is the one ordering property that survives re-registration, so it is pinned.",
        )

    def test_the_snapshot_is_not_empty(self):
        rows = [l for l in SNAPSHOT.read_text(encoding="utf-8").splitlines() if l.strip()]
        self.assertGreater(len(rows), 100, "the metadata snapshot looks truncated")




class DomainRouterHarnessTests(unittest.TestCase):
    """The harness every route domain must be built with.

    v0.5.2a. `JsonApiRoute` carries the bounded SQLite write-lock retry and is configured on the
    ROUTER, not the decorator — so a domain built with a bare `APIRouter()` keeps its bodies, paths
    and methods, passes every existing test, and silently loses lock-retry. `domain_router()` exists
    so that is not something a domain can get wrong by hand.
    """

    def test_the_factory_fixes_the_lock_retry_route_class(self):
        from service.api_core.routing import JsonApiRoute, domain_router

        self.assertIs(domain_router().route_class, JsonApiRoute)

    def test_the_factory_refuses_an_override_rather_than_honouring_it(self):
        """Opting out has to be impossible, not merely discouraged."""
        from fastapi.routing import APIRoute

        from service.api_core.routing import domain_router

        with self.assertRaises(TypeError) as caught:
            domain_router(route_class=APIRoute)
        self.assertIn("write-lock retry", str(caught.exception))

    def test_the_factory_still_forwards_ordinary_router_options(self):
        """A harness nobody can configure is a harness nobody will use."""
        from service.api_core.routing import domain_router

        router = domain_router(prefix="/api/v1/thing", tags=["thing"])
        self.assertEqual(router.prefix, "/api/v1/thing")
        self.assertEqual(router.tags, ["thing"])




class RouteAnnotationsResolveTests(unittest.TestCase):
    """A body model that fails to resolve becomes a QUERY parameter, and only 422s at request time.

    THE DOMAIN-PHASE TRAP, hit for real moving spawn-requests. `claim_spawn_request(req:
    SpawnRequestClaim)` moved to a new module whose imports did not include that model. Because the
    codebase uses `from __future__ import annotations`, the annotation is just the STRING
    "SpawnRequestClaim" — so:

      - `py_compile` passes: nothing is undefined at compile time;
      - the module imports fine: the name is never evaluated;
      - the undefined-name sweep sees NOTHING, because there is no Name node to find;
      - `create_app()` succeeds and the route exists with the right path, method and route_class;
      - the route metadata snapshot is UNCHANGED, because none of the fields it pins move.

    FastAPI then cannot resolve the annotation, falls back to treating `req` as a query parameter,
    and the endpoint 422s on every real call. Eighty-five tests went red and every static check in
    this repo was green.

    So the resolution is checked directly: every route endpoint's type hints must evaluate, and any
    parameter annotated with a Pydantic model must be a BODY parameter and never a query one.
    """

    def test_every_route_endpoints_annotations_resolve(self):
        import typing

        from service.main import create_app

        app = create_app()
        broken = []
        for route in app.routes:
            endpoint = getattr(route, "endpoint", None)
            if endpoint is None or not getattr(route, "path", None):
                continue
            try:
                typing.get_type_hints(endpoint)
            except Exception as error:  # noqa: BLE001 - the point is to report ANY failure
                broken.append(f"{route.path} {endpoint.__name__}: {type(error).__name__}: {error}")
        self.assertEqual(
            broken,
            [],
            "A route handler's annotations do not resolve. With postponed annotations this does "
            "NOT fail at import — FastAPI silently reinterprets the parameter and the endpoint "
            "breaks at request time:\n  " + "\n  ".join(broken),
        )

    def test_no_pydantic_model_is_being_treated_as_a_query_parameter(self):
        """The precise symptom: a body model demoted to `?req=`."""
        from pydantic import BaseModel

        from service.main import create_app

        app = create_app()
        offenders = []
        for route in app.routes:
            dependant = getattr(route, "dependant", None)
            endpoint = getattr(route, "endpoint", None)
            if dependant is None or endpoint is None:
                continue
            import typing

            try:
                hints = typing.get_type_hints(endpoint)
            except Exception:
                continue  # reported by the test above
            for param in getattr(dependant, "query_params", []):
                annotation = hints.get(param.name)
                if isinstance(annotation, type) and issubclass(annotation, BaseModel):
                    offenders.append(
                        f"{route.path} {endpoint.__name__}: `{param.name}: "
                        f"{annotation.__name__}` is a QUERY param, so the body is never read"
                    )
        self.assertEqual(
            offenders,
            [],
            "A request-body model is being read as a query parameter. Almost always a model that "
            "was not imported into a module a handler moved to:\n  " + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
