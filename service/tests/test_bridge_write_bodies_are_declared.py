"""Every field the bridge POSTs must be one the service declared, or it is dropped in silence.

THE JOIN NOTHING WATCHED. The bridge runs on the host, the service runs in the container, and the
only thing between them is a JSON body. Pydantic's default `extra` is "ignore", so a key the route's
model does not declare is discarded without an error on either side: the bridge's log says it
reported, the service's row says nothing happened, and both are telling the truth. That is the same
shape as every defect this review has found -- a producer emitting one thing while a consumer reads
another -- and it is the shape that took a whole day to see when `exit_code` had a producer and no
column.

MEASURED FIRST, 2026-08-26: 98 write call sites in `mcp/stdio`, 88 with a body this scan can read,
all 88 resolving to a declared route, and ZERO undeclared keys. This gate freezes that, so the first
key that stops being read fails here rather than in a month's debugging.

IT ALSO PROVES THE PATH EXISTS. A bridge posting to a path no router serves gets an HTTP 404 that
most of these call sites swallow as "best effort" -- so a renamed route presents as a feature that
quietly stopped, not as an error. The path check is the cheaper half of this file and catches the
louder bug.

WHAT IT CANNOT SEE, and says so rather than implying coverage: a body passed as a VARIABLE
(`httpCall("POST", path, payload)`) has no literal keys to read. There are ten, each named in
`BODIES_BUILT_ELSEWHERE` with the builder that produces it. They are listed rather than skipped so
the blind spot is visible in the source instead of hiding inside a clean number.
"""

from __future__ import annotations

import re
import typing
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
#: Where bridge code lives. Named explicitly rather than walked recursively: `mcp/stdio/scripts/` is
#: a developer tool and `mcp/stdio/tests/` holds fixtures that deliberately contain wrong shapes.
BRIDGE_DIRS = ("mcp/stdio", "mcp/stdio/adapters", "mcp/stdio/controllers")

#: The write helpers. `httpCall` is the shared one; `call` is its injected alias in every handler
#: that takes one for testing; `_call` and `post` are the two method spellings on client classes.
_WRITE_CALL = re.compile(r"\b(?:call|httpCall|_call|post)\(\s*([\"'])(POST|PATCH|PUT)\1\s*,\s*")

#: Call sites whose body is a variable or a function call, so this scan reads no keys from them.
#: `file:line` -> the builder that decides the body. Kept exact: an entry that no longer matches
#: fails the census test below, so this list cannot rot into a stale excuse.
BODIES_BUILT_ELSEWHERE = {
    "mcp/stdio/agent-heartbeat.mjs": "agentHeartbeatPayload / currentTurnHeartbeatFields",
    "mcp/stdio/auto-registration.mjs": "the registration payload built above the call",
    "mcp/stdio/registration-tool.mjs": "agentData, assembled from the tool's arguments",
    "mcp/stdio/required-reply-handoff.mjs": "the owed-reply body built above the call",
    "mcp/stdio/run-callbacks.mjs": "the external-refs patch built from what the runtime returned",
    "mcp/stdio/server.js": "environmentHeartbeatPayload / the usage payload",
    "mcp/stdio/virtual-terminals.mjs": "updateTerminalControl's caller supplies the patch",
}


# --------------------------------------------------------------------------------------------
# Reading JavaScript. A regex stops at the first comma inside a nested object, so these walk.
# --------------------------------------------------------------------------------------------

def _strip_comments(src: str) -> str:
    """Remove comments, keeping newlines so reported line numbers stay the source's own.

    NOT COSMETIC. An apostrophe in a comment ("doesn't") opens a string for the argument walk and
    swallows the rest of the call -- which is how `if`, `catch` and `const` arrived as body keys the
    first time this was measured.
    """
    out = []
    i = 0
    while i < len(src):
        c = src[i]
        if c in "\"'`":
            quote = c
            out.append(c)
            i += 1
            while i < len(src):
                if src[i] == "\\":
                    out.append(src[i : i + 2])
                    i += 2
                    continue
                out.append(src[i])
                if src[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if c == "/" and src[i : i + 2] == "//":
            while i < len(src) and src[i] != "\n":
                i += 1
            continue
        if c == "/" and src[i : i + 2] == "/*":
            i += 2
            while i < len(src) and src[i : i + 2] != "*/":
                if src[i] == "\n":
                    out.append("\n")
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _read_argument(src: str, i: int) -> tuple[str, int, str]:
    """One argument from `i`, respecting nesting and strings. Returns (text, end, terminator)."""
    depth = 0
    start = i
    while i < len(src):
        c = src[i]
        if c in "\"'`":
            quote = c
            i += 1
            while i < len(src):
                if src[i] == "\\":
                    i += 2
                    continue
                if src[i] == quote:
                    break
                if quote == "`" and src[i : i + 2] == "${":
                    braces = 1
                    i += 2
                    while i < len(src) and braces:
                        braces += (src[i] == "{") - (src[i] == "}")
                        i += 1
                    continue
                i += 1
            i += 1
            continue
        if c in "([{":
            depth += 1
        elif c in ")]}":
            if depth == 0:
                return src[start:i], i, c
            depth -= 1
        elif c == "," and depth == 0:
            return src[start:i], i, ","
        i += 1
    return src[start:], i, ""


_KEY = re.compile(r"^(?:[\"']([^\"']+)[\"']|\[([^\]]+)\]|([A-Za-z_$][\w$]*))")


def _top_level_keys(obj_src: str) -> list[str] | None:
    """The top-level keys of an object literal, or None if this is not one.

    A spread is reported as "..." and never treated as a key: it carries whatever its source held,
    which this scan cannot know, and inventing a name for it would be a fact nobody stated.
    """
    body = obj_src.strip()
    if not (body.startswith("{") and body.endswith("}")):
        return None
    inner = body[1:-1]
    keys: list[str] = []
    i = 0
    while i < len(inner):
        while i < len(inner) and inner[i] in " \t\r\n,":
            i += 1
        if i >= len(inner):
            break
        if inner.startswith("...", i):
            keys.append("...")
        else:
            match = _KEY.match(inner[i:])
            if match:
                keys.append(match.group(1) or match.group(3) or f"[{match.group(2)}]")
        _, end, _ = _read_argument(inner, i)
        i = end + 1
    return keys


def _normalise(path_expr: str) -> str:
    """A call's path expression or a route's path, reduced to the same comparable shape."""
    text = path_expr.strip().strip("`\"'")
    text = re.sub(r"\$\{[^}]*\}", "*", text)  # bridge interpolation
    text = re.sub(r"\{[^}]+\}", "*", text)  # FastAPI parameter
    text = re.sub(r"^/api/v1", "", text)  # httpCall prepends it
    return text.split("?")[0].rstrip("/") or "/"


class CallSite(typing.NamedTuple):
    file: str
    line: int
    method: str
    path: str
    keys: list[str] | None


def _bridge_write_calls() -> list[CallSite]:
    sites: list[CallSite] = []
    for rel_dir in BRIDGE_DIRS:
        directory = REPO / rel_dir
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.suffix not in (".js", ".mjs") or ".test." in path.name:
                continue
            src = _strip_comments(path.read_text(encoding="utf-8"))
            for match in _WRITE_CALL.finditer(src):
                path_arg, after_path, terminator = _read_argument(src, match.end())
                body_arg = ""
                if terminator == ",":
                    body_arg, _, _ = _read_argument(src, after_path + 1)
                sites.append(CallSite(
                    file=f"{rel_dir}/{path.name}",
                    line=src[: match.start()].count("\n") + 1,
                    method=match.group(2),
                    path=_normalise(path_arg),
                    keys=_top_level_keys(body_arg),
                ))
    return sites


# --------------------------------------------------------------------------------------------
# Reading the service. By BUILDING THE APP, because the routes come from 15 routers and a scan of
# decorators would report whichever half it happened to walk.
# --------------------------------------------------------------------------------------------

def _declared_write_routes() -> dict[tuple[str, str], set[str] | None]:
    """(method, path) -> the field names its body model accepts, or None for a raw-body route.

    None means the handler takes the `Request` and reads the JSON itself, so it accepts any key and
    this gate has nothing to check. That is a real answer, not a gap: those routes have no declared
    vocabulary to drift from.
    """
    from fastapi.routing import APIRoute
    from pydantic import BaseModel

    from service.main import create_app

    routes: dict[tuple[str, str], set[str] | None] = {}
    for route in create_app().routes:
        if not isinstance(route, APIRoute):
            continue
        methods = {m for m in route.methods if m in {"POST", "PATCH", "PUT"}}
        if not methods:
            continue
        declared: set[str] = set()
        found_model = False
        for name, annotation in typing.get_type_hints(route.endpoint).items():
            if name == "return":
                continue
            candidates = list(typing.get_args(annotation)) or [annotation]
            for candidate in candidates:
                if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                    found_model = True
                    for field_name, field in candidate.model_fields.items():
                        declared.add(field_name)
                        if field.alias:
                            declared.add(field.alias)
        key_path = _normalise(route.path)
        for method in methods:
            routes[(method, key_path)] = declared if found_model else None
    return routes


class BridgeWriteBodyTests(unittest.TestCase):
    """The bridge's write bodies against the service's declared fields."""

    @classmethod
    def setUpClass(cls):
        cls.sites = _bridge_write_calls()
        cls.routes = _declared_write_routes()

    def test_every_write_the_bridge_makes_reaches_a_route_that_exists(self):
        """A POST to a path nothing serves is a 404 these call sites mostly swallow."""
        missing = [
            f"{s.file}:{s.line} {s.method} {s.path}"
            for s in self.sites
            if (s.method, s.path) not in self.routes
        ]
        self.assertEqual(missing, [], (
            "the bridge writes to paths the service does not serve:\n  " + "\n  ".join(missing)
            + "\nMost of these calls treat a failure as best-effort, so a renamed route presents as "
            "a feature that quietly stopped rather than as an error."
        ))

    def test_no_body_key_is_dropped_because_the_model_never_declared_it(self):
        offenders = []
        for site in self.sites:
            if site.keys is None:
                continue
            declared = self.routes.get((site.method, site.path))
            if not declared:  # unknown path (covered above) or a raw-body route
                continue
            unknown = [
                key for key in site.keys
                if key != "..." and not key.startswith("[") and key not in declared
            ]
            if unknown:
                offenders.append(
                    f"{site.file}:{site.line} {site.method} {site.path} sends "
                    f"{', '.join(unknown)} -- not declared by the route model"
                )
        self.assertEqual(offenders, [], (
            "these fields are discarded on arrival:\n  " + "\n  ".join(offenders)
            + "\nPydantic's default `extra` is 'ignore', so nothing raises: the bridge reports and "
            "the service records nothing. Declare the field on the request model in the same commit "
            "as the bridge change, or stop sending it."
        ))

    def test_the_scan_read_both_sides_and_can_answer_either_way(self):
        """A comparison that found nothing is indistinguishable from one that compared nothing."""
        self.assertGreater(len(self.sites), 80, f"only {len(self.sites)} write call sites found")
        self.assertGreater(len(self.routes), 50, f"only {len(self.routes)} write routes built")
        readable = [s for s in self.sites if s.keys is not None]
        self.assertGreater(len(readable), 70, f"only {len(readable)} bodies parsed as literals")
        modelled = [
            s for s in readable if self.routes.get((s.method, s.path))
        ]
        self.assertGreater(len(modelled), 50, f"only {len(modelled)} land on a route with a model")

        # POSITIVE CONTROL: a key that IS declared must be recognised, or every key reads as unknown
        # and the gate above would be failing rather than passing.
        sample = modelled[0]
        declared = self.routes[(sample.method, sample.path)]
        self.assertTrue(
            [k for k in sample.keys if k in declared],
            f"{sample.file}:{sample.line} sends {sample.keys} and the matcher recognised none of "
            "them — the comparison is broken, not the code",
        )
        # NEGATIVE CONTROL: a name no model could declare must be reported as unknown.
        self.assertNotIn("zzNoModelDeclaresThiszz", declared)

    def test_the_parser_reads_the_shapes_this_repo_actually_writes(self):
        """Anti-vacuity on the walk itself: a shape it cannot see is a shape it cannot check."""
        self.assertEqual(_top_level_keys('{ a: 1, b: { c: 2, d: 3 }, e: 4 }'), ["a", "b", "e"])
        self.assertEqual(_top_level_keys('{ "quoted-key": 1, plain: 2 }'), ["quoted-key", "plain"])
        self.assertEqual(_top_level_keys("{ ...base, status: 'x' }"), ["...", "status"])
        self.assertEqual(_top_level_keys("{ a: f(1, 2), b: [3, 4] }"), ["a", "b"])
        self.assertEqual(_top_level_keys("{ a: `t ${x ? 1 : 2} u`, b: 1 }"), ["a", "b"])
        self.assertIsNone(_top_level_keys("payload"), "a variable body must not parse as a literal")
        # A comment must never become a key, and its apostrophe must not swallow the call.
        self.assertEqual(
            _top_level_keys(_strip_comments("{ a: 1, /* it doesn't count */ b: 2 }")), ["a", "b"])
        self.assertEqual(_normalise("`/agents/${encodeURIComponent(id)}/heartbeat`"),
                         _normalise("/api/v1/agents/{agent_id}/heartbeat"))

    def test_the_bodies_this_scan_cannot_read_are_named(self):
        """The blind spot is written down, so a clean result cannot be read as full coverage."""
        opaque = sorted({s.file for s in self.sites if s.keys is None})
        self.assertEqual(opaque, sorted(BODIES_BUILT_ELSEWHERE), (
            "the set of call sites whose body is built elsewhere changed.\n"
            f"  now: {opaque}\n  recorded: {sorted(BODIES_BUILT_ELSEWHERE)}\n"
            "A new one means a body this gate cannot check — name its builder here, and check by "
            "hand that what the builder emits is declared. One that disappeared means a body became "
            "readable, so delete its line."
        ))


if __name__ == "__main__":
    unittest.main()
