"""The `environment_heartbeat` split, re-proved against the real code on every run.

Same shape as the other split proofs here: proving a split once at refactor time proves the commit,
running the round trip in the suite proves it STAYS true.

WHAT WAS EXTRACTED: telling a superseded environment bridge to stop, and draining the stop requests
that were never claimed because the bridge it was addressed to had already died.

THE CONSTANT TRAVELLED WITH IT. `SUPERSEDE_STOP_STALE_SECONDS` had exactly one reader — this block —
and a constant whose only use is in another module is a fork waiting to happen. The round trip cannot
see that (it only reconstructs the names in EXTRACTIONS), so it is asserted separately.

THE SUBSTITUTION, declared rather than left to be noticed: the helper lives in
`service/api_core/superseded_bridge_stops.py`, because leaving it in the router would not have
reduced it — that was the point. The extract-method gate needs the caller and the helper in one tree,
so the sources are CONCATENATED for the proof.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
ENVIRONMENTS = REPO / "service" / "routers" / "environments.py"
STOPS = REPO / "service" / "api_core" / "superseded_bridge_stops.py"
#: The upsert got its own module: it records, it refuses nothing, and the two-statement
#: relationship it carries deserves to be explained where it lives.
REGISTRATION = REPO / "service" / "api_core" / "environment_registration.py"
FIXTURE = Path(__file__).resolve().parent / "data" / "environment_heartbeat_before_split.py"

SOURCE_FUNCTION = "environment_heartbeat"
EXTRACTIONS = [
    "_queue_stop_for_superseded_bridge",
    "_record_environment_registration",
]

#: Where each helper is expected to be declared. PER HELPER, over every module below.
OWNERS = {
    "_queue_stop_for_superseded_bridge": STOPS,
    "_record_environment_registration": REGISTRATION,
}

MODULES = (ENVIRONMENTS, STOPS, REGISTRATION)

TRAVELLING_CONSTANT = "SUPERSEDE_STOP_STALE_SECONDS"


def _combined_split_source() -> str:
    """The caller and every extracted helper in one tree, for the inline-back comparison."""
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


def _declared(path: Path) -> set[str]:
    return {
        n.name for n in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _module_constants(path: Path) -> set[str]:
    return {
        t.id for n in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(n, ast.Assign) for t in n.targets if isinstance(t, ast.Name)
    }


#: Edits to the extracted helpers made AFTER the split, undone before the round trip is compared.
#: The fixture is a frozen record of the function as it stood before the extraction, so a later and
#: entirely unrelated change to a helper breaks the inline unless it is declared here.
#:
#: `%Y-%m-%dT%H:%M:%SZ` was typed out at eleven product sites while `service/clock.py` already
#: declared it as `ISO_SECONDS` -- and that module's own docstring says stored timestamps are
#: compared LEXICALLY in SQL throughout, so a site that drifts produces a comparison that is wrong
#: with no error. The helper below is one of the ten that now import the constant.
#: DECLARED EDIT, 2026-08-29. An environment-tier advertisement describes the HOST and declares
#: no `bridgeId`. Both inputs to the supersede arbitration were being erased by it -- the id, by
#: `req.bridgeId or ""` on the UPDATE, and `bridgeStartedAt`, because `next_metadata` REPLACES
#: the stored metadata. Either one alone disarms the guard. The UPDATE now preserves what is on
#: the row and the metadata merge keeps the bridge-owned keys. Undone here rather than
#: re-captured, so the pre-split baseline survives.
#: DECLARED EDIT, 2026-08-29. The service now JOINS `kind` + `hostname` into the environment id
#: when a caller sends no `id`, so the string is built once in the tier whose table it keys. An
#: advertiser deriving it independently would agree the day it was written and mint a DUPLICATE
#: environment the first time either copy of the rule changed. Undone here rather than
#: re-captured, so the pre-split baseline survives.
#: DECLARED EDIT, 2026-08-30. One `_kept()` rule for seven fields: a heartbeat does not blank
#: what it said nothing about. `req.X or ""` turned an omitted field into an erased one, which
#: is how an id-less beat disarmed supersession in 02045701 -- that fix covered the two fields
#: the guard reads, and five more had the same shape. Undone here rather than re-captured, so
#: the pre-split baseline survives.
EDITED_SINCE = [
    (
        # ADDED, declared as a deletion for the same reason as the entry below it: it sits
        # between two lines a later entry declares as adjacent.
        "        # And the host's own answers, for a caller that described no host. Keyed on the request field\n        # being absent: a caller that sent `terminal: false` is making a claim and is believed.\n        for request_field, metadata_key in HOST_OWNED_METADATA:\n            if getattr(req, request_field, None) is None and metadata_key in existing_metadata:\n                next_metadata.setdefault(metadata_key, existing_metadata[metadata_key])\n",
        '',
    ),
    (
        # ADDED, so declared as a deletion: the reconstruction removes it to recover the file the
        # fixture recorded. It sits between two lines a later entry declares as adjacent, which is
        # why it needs its own entry and why that entry has to come first.
        'def _canonical_runtimes(rows: Any) -> list:\n    """Runtime rows with their names put through the shared vocabulary.\n\n    THE SERVICE OWNS THE VOCABULARY, and this is the half it was not doing. A host sends the names it\n    can see on disk -- `claude`, `omp` -- because `service/contracts/vocabulary.json` already maps them\n    in both languages with an agreement test per side, and a second copy of that map in the environment\n    tier is exactly the drift the contract exists to prevent.\n\n    NOT A CORRECTNESS FIX. Both readers of these rows normalise both sides already, so a stored\n    `claude` matches a lookup for `claude-code`. What it fixes is a row that reads `claude` while every\n    agent on it reads `claude-code` -- two screens that agree only if you know the alias table.\n\n    Idempotent: `claude-code` maps to itself, so a bridge sending canonical names is unaffected. A row\n    that is not a dict is passed through rather than dropped, because inventing a shape is worse than\n    storing an odd one, and the readers all use `.get`.\n    """\n    if not isinstance(rows, list):\n        return rows\n    canonical = []\n    for row in rows:\n        if not isinstance(row, dict):\n            canonical.append(row)\n            continue\n        name = _normalize_runtime(row.get("runtime"))\n        canonical.append({**row, "runtime": name} if name else row)\n    return canonical\n\n\n',
        '',
    ),
    (
        '    #: `is None`, NOT falsiness. For a list, "said nothing" and "said there are none" are different\n    #: claims, and `or []` collapsed them -- so a heartbeat that omitted either field erased it. The\n    #: stored value is restored below, once `existing` has been read.\n    cwd_roots = _normalize_roots(req.cwdRoots) if req.cwdRoots is not None else None\n    runtimes = _canonical_runtimes(req.runtimes) if req.runtimes is not None else None',
        '    cwd_roots = _normalize_roots(req.cwdRoots or [])\n    runtimes = req.runtimes or []',
    ),
    (
        '        #: Three cases, and the middle one is the fix. Manual roots always win; a caller that said\n        #: nothing keeps what the row holds; a caller that spoke is believed, including when it says\n        #: the list is empty.\n        stored_roots = _json_loads_or(existing["cwd_roots"], []) if existing else []\n        if manual_roots and existing:\n            effective_roots = stored_roots\n        elif cwd_roots is None:\n            effective_roots = stored_roots\n        else:\n            effective_roots = cwd_roots\n        #: Same rule for the runtimes. Blanking these is the worst of the set: an environment with no\n        #: runtimes advertises nothing that can be spawned on it.\n        if runtimes is None:\n            runtimes = _json_loads_or(existing["runtimes"], []) if existing else []\n        #: What the CALLER advertised, which is a different fact from what the row now holds. A\n        #: caller that said nothing has not advertised an empty list, so the previous claim stands.\n        advertised_roots = cwd_roots if cwd_roots is not None else (\n            existing_metadata.get("advertisedCwdRoots", []) if existing else []\n        )\n        next_metadata = {**metadata, "advertisedCwdRoots": advertised_roots}',
        '        effective_roots = _json_loads_or(existing["cwd_roots"], []) if existing and manual_roots else cwd_roots\n        next_metadata = {**metadata, "advertisedCwdRoots": cwd_roots}',
    ),
    (
        '            # The INSERT below keeps `or ""`: a row being created has no prior bridge to preserve.\n            #: ONE RULE, SEVEN FIELDS. Each is `Optional[...] = None` on the request model, so a\n            #: caller that omits one has said nothing about it -- and `req.X or ""` turned that\n            #: silence into an erasure. The model already states the distinction for roots: "null\n            #: means the service said nothing about roots -- keep what we had. An empty ARRAY means\n            #: it said there are none."\n            def _kept(incoming, column):\n                return str(incoming or "").strip() or str(existing[column] or "")\n\n            # LABEL BELONGS IN THE SET AFTER ALL, and the reason it was left out was wrong. The\n            # argument was that `req.label or env_id` falls back to a real default rather than a\n            # blank -- true, and irrelevant on an UPDATE: the "real default" is the raw environment\n            # id, so one advertisement that says nothing about the label replaces the operator\'s\n            # "Windows on StevenZ-L" with "windows:StevenZ-L:default". Measured, not reasoned about.\n            #\n            # The INSERT below keeps `req.label or env_id`: a row being created has no prior label,\n            # and its id is the honest name until somebody gives it one.\n            preserved_label = _kept(req.label, "label")\n\n            preserved_bridge_id = _kept(req.bridgeId, "bridge_id")\n            preserved_bridge_version = _kept(req.bridgeVersion, "bridge_version")\n            preserved_machine_id = _kept(req.machineId, "machine_id")\n            preserved_os = _kept(req.os, "os")\n            preserved_kind = _kept(req.kind, "kind")\n            preserved_launcher_version = _kept(req.launcherVersion, "launcher_version")\n            preserved_launcher_fingerprint = _kept(\n                req.launcherRegistryFingerprint, "launcher_registry_fingerprint"\n            )',
        '            # The INSERT below keeps `or ""`: a row being created has no prior bridge to preserve.\n            preserved_bridge_id = str(req.bridgeId or "").strip() or str(existing["bridge_id"] or "")\n            preserved_bridge_version = (\n                str(req.bridgeVersion or "").strip() or str(existing["bridge_version"] or "")\n            )',
    ),
    (
        '                    preserved_label or env_id,\n                    preserved_machine_id,\n                    preserved_os,\n                    preserved_kind,\n                    preserved_bridge_id,',
        '                    req.label or env_id,\n                    req.machineId or "",\n                    req.os or "",\n                    req.kind or "",\n                    preserved_bridge_id,',
    ),
    (
        '                    preserved_bridge_version,\n                    preserved_launcher_version,\n                    preserved_launcher_fingerprint,\n                    json.dumps(effective_roots),',
        '                    preserved_bridge_version,\n                    req.launcherVersion or "",\n                    req.launcherRegistryFingerprint or "",\n                    json.dumps(effective_roots),',
    ),
    (
        '\ndef _derived_environment_id(kind: Any, hostname: Any) -> str:\n    """`kind:hostname:default`, the id a caller may omit.\n\n    ONE IMPLEMENTATION, HERE. The join keys this service\'s own table, and a second advertiser that\n    built the same string itself would agree until either copy of the rule changed -- at which point\n    it would not fail, it would create a DUPLICATE environment beside the real one and leave the\n    managed agents bound to whichever the bridge wrote.\n\n    `kind` is host knowledge the service cannot compute: it distinguishes wsl, docker, windows,\n    macos and linux from environment variables and `/.dockerenv` on the host itself. So the host\n    sends the two facts and the service performs the join.\n\n    THE HOSTNAME IS NOT LOWERCASED, and that is inherited rather than chosen. The live row is\n    `windows:StevenZ-L:default` while its `machineId` is `win32:stevenz-l` -- the service normalises\n    machineId with a field validator and has never normalised this. Lowercasing here would mint a\n    new id for every existing environment and orphan the agents bound to the old one.\n\n    Returns "" when either fact is missing, so the caller\'s own "id is required" refusal still fires\n    rather than a half-built id like `windows::default` reaching the table.\n    """\n    kind_text = str(kind or "").strip()\n    host_text = str(hostname or "").strip()\n    if not kind_text or not host_text:\n        return ""\n    return f"{kind_text}:{host_text}:default"\n\n\n#: What only a BRIDGE can know about itself, and therefore what an advertisement must leave alone.',
        '\n#: What only a BRIDGE can know about itself, and therefore what an advertisement must leave alone.',
    ),
    (
        'async def environment_heartbeat(req: EnvironmentHeartbeat, request: Request):\n    env_id = str(req.id or "").strip() or _derived_environment_id(req.kind, req.hostname)\n    if not env_id:',
        'async def environment_heartbeat(req: EnvironmentHeartbeat, request: Request):\n    env_id = str(req.id or "").strip()\n    if not env_id:',
    ),
    (
        '# it travelled with the drain that was its only reader.\n\n\n#: What only a BRIDGE can know about itself, and therefore what an advertisement must leave alone.\n#:\n#: An environment-tier heartbeat describes the HOST -- runtimes, roots, terminal availability -- and\n#: declares no `bridgeId`. Its metadata carries no `bridgeStartedAt` either, and `next_metadata`\n#: REPLACES the stored metadata, so an advertisement erased the timestamp the supersede arbitration\n#: reads. Preserving `bridge_id` alone was not enough: with two ids and no start times, the branch\n#: that refuses an OLDER incoming bridge cannot fire, and a stale bridge reclaims the environment a\n#: fresh one owns.\n#:\n#: DERIVED FROM ITS READERS, not guessed: `bridgeStartedAt` is read by `_bridge_started_at` here and\n#: by `environment_claim.py`, and nothing else in the service reads a `bridge*` metadata key. A\n#: second one belongs in this tuple the day it gets a reader.\nBRIDGE_OWNED_METADATA = ("bridgeStartedAt",)\n',
        '# it travelled with the drain that was its only reader.\n',
    ),
    (
        '        if not str(req.bridgeId or "").strip():\n            for bridge_key in BRIDGE_OWNED_METADATA:\n                if bridge_key in existing_metadata and bridge_key not in next_metadata:\n                    next_metadata[bridge_key] = existing_metadata[bridge_key]\n        if manual_roots:',
        '        if manual_roots:',
    ),
    (
        '        if existing:\n            # A HEARTBEAT THAT DECLARES NO BRIDGE KEEPS THE ONE ON THE ROW.\n            #\n            # `bridgeId` is optional on `EnvironmentHeartbeat`, and the model already says why: "a\n            # bridge started by hand has no launcher and sends neither; that is normal rather than\n            # missing data". The environment TIER advertising its own capabilities is the same\n            # shape -- it describes the host, it does not claim to own the bridge.\n            #\n            # Writing `req.bridgeId or ""` blanked the column for those callers, and the blanking is\n            # not the expensive half. Supersession is gated on BOTH sides carrying an id:\n            #\n            #     if existing and existing["bridge_id"].strip() and req.bridgeId.strip():\n            #\n            # so ONE id-less heartbeat disarms the arbitration between a stale bridge and a fresh\n            # one, permanently and silently, and the `bridgeStartedAt` comparison behind it never\n            # runs again. A guard whose input has been erased reads exactly like a guard with\n            # nothing to arbitrate.\n            #\n            # The INSERT below keeps `or ""`: a row being created has no prior bridge to preserve.\n            preserved_bridge_id = str(req.bridgeId or "").strip() or str(existing["bridge_id"] or "")\n            preserved_bridge_version = (\n                str(req.bridgeVersion or "").strip() or str(existing["bridge_version"] or "")\n            )\n            await db.execute(',
        '        if existing:\n            await db.execute(',
    ),
    (
        '                    req.kind or "",\n                    preserved_bridge_id,\n                    preserved_bridge_version,\n                    req.launcherVersion or "",',
        '                    req.kind or "",\n                    req.bridgeId or "",\n                    req.bridgeVersion or "",\n                    req.launcherVersion or "",',
    ),
    ("            ).strftime(ISO_SECONDS)", '            ).strftime("%Y-%m-%dT%H:%M:%SZ")'),
]


class EnvironmentHeartbeatSplitIsInertTests(unittest.TestCase):
    def test_the_extraction_inlines_back_to_the_original(self):
        fixture_src = FIXTURE.read_text(encoding="utf-8")
        original = next(
            n for n in ast.parse(fixture_src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )
        assert_extractions_preserve_behaviour(
            ast.get_source_segment(fixture_src, original), _combined_split_source(), EXTRACTIONS,
            edited_since=EDITED_SINCE)

    def test_the_fixture_is_the_function_it_claims_to_be(self):
        """A fixture that stopped containing the function would make the test above vacuous."""
        self.assertIn(SOURCE_FUNCTION, _declared(FIXTURE))

    def test_the_fixture_was_not_captured_with_a_mangled_decode(self):
        """`subprocess.run(text=True)` decodes with the Windows locale and mangles every dash."""
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("�", text, "fixture contains U+FFFD replacement characters")
        self.assertGreater(text.count("—"), 0, "fixture looks locale-mangled, not utf-8")

    def test_the_helper_is_not_still_inline(self):
        """If the split were reverted, the round trip would pass by having nothing to inline."""
        for helper in EXTRACTIONS:
            self.assertNotIn(
                helper, _declared(ENVIRONMENTS),
                f"{helper} is back in environments.py; this proof is vacuous")

    def test_exactly_one_module_declares_EACH_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [path for path in MODULES if helper in _declared(path)]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once, in {owner.name}")

    def test_the_leaf_does_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent."""
        for leaf in (STOPS, REGISTRATION):
            for node in ast.walk(ast.parse(leaf.read_text(encoding="utf-8"))):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(
                        node.module.startswith("service.routers")
                        or node.module == "service.control_plane",
                        f"{leaf.name} imports upward from {node.module}",
                    )

    def test_the_constant_TRAVELLED_and_did_not_fork(self):
        """Exactly one declaration, in the module that reads it.

        The round trip cannot see this: it reconstructs the names in EXTRACTIONS, so a copy of the
        constant left behind in the router would keep the proof green while the two drifted — and a
        drifted TTL is silent, since both values produce a plausible drain.
        """
        self.assertIn(TRAVELLING_CONSTANT, _module_constants(STOPS))
        self.assertNotIn(
            TRAVELLING_CONSTANT, _module_constants(ENVIRONMENTS),
            "the constant is declared in both modules; one of them will go stale")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
