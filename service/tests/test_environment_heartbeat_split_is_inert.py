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
        # DECLARED EDIT, 2026-09-06. The `bridge_version` COLUMN was fed from `req.bridgeVersion`,
        # which nothing has sent since v0.6.2 deleted the environment-bridge cluster -- aify-env
        # puts its version in `metadata` with the rest of its identity. Measured on the operator's
        # host: metadata said 0.6.2, the column said 0.6.0, and `tier-version` read the column.
        # Undone here rather than re-captured, so the pre-split baseline survives.
        '        #: WHERE THE VERSION ACTUALLY ARRIVES, which is not where this column was reading it.\n        #:\n        #: MEASURED 2026-09-06 on the operator\'s own host: `metadata.bridgeVersion` read `0.6.2` and\n        #: the `bridge_version` COLUMN read `0.6.0`, on one row, written by one live claimer. So\n        #: `tier-version` reported the host tier two versions behind while aify-env was current --\n        #: the exact false red that makes a check get switched off.\n        #:\n        #: aify-env sends `bridgeId` TOP-LEVEL and the rest of its identity inside `metadata`, and\n        #: `api.mjs` says why in its own comment: sending `bridgeStartedAt` at the top level looked\n        #: right and was silently ignored, because the arbitration reads it from `metadata`.\n        #: `bridgeVersion` rides in that same identity object -- and this column was fed from\n        #: `req.bridgeVersion`, which NOTHING has sent since v0.6.2 deleted the environment-bridge\n        #: cluster. `_kept()` then did its job perfectly: it preserved the last value a legacy bridge\n        #: wrote, and froze it there for good.\n        #:\n        #: BOTH ENDS OF THE FIELD, which is this repo\'s own rule: a value with no reader and a reader\n        #: with no writer are one defect from opposite sides. The identity\'s home is `metadata`; the\n        #: column is its projection, so it reads from where the value lives.\n        #:\n        #: SAFE BECAUSE OWNERSHIP IS ALREADY ENFORCED AT THE BOUNDARY. `environment_heartbeat` drops\n        #: the caller\'s whole `bridge*` namespace when it sends no `bridgeId`, so a value reaching\n        #: `next_metadata` here has already proved it came from a claimer. An advertiser cannot\n        #: forge one.\n        incoming_bridge_version = (\n            str(req.bridgeVersion or "").strip()\n            or str((next_metadata or {}).get("bridgeVersion") or "").strip()\n        )\n',
        '',
    ),
    (
        # The two call sites that read it. Same edit, declared where they sit.
        '            preserved_bridge_version = _kept(incoming_bridge_version, "bridge_version")',
        '            preserved_bridge_version = _kept(req.bridgeVersion, "bridge_version")',
    ),
    (
        '                    incoming_bridge_version,',
        '                    req.bridgeVersion or "",',
    ),
    (
        # DECLARED ADDITION, 2026-09-04 (external review, Round 8 H4). Supersession arbitrated on
        # START TIME alone, so a retired aify-comms environment bridge on a host that had not
        # re-run install.sh took the row from the aify-env host tier simply by starting later --
        # and then became the only party allowed to claim a spawn. The service had no signal to
        # ignore: both identities carry the same three fields, so aify-env now sends
        # `metadata.bridgeKind` and this reads it. Declared as a deletion so the pre-split
        # baseline survives, like every entry above.
        '                # THE HOST TIER OUTRANKS A BRIDGE, whatever the start times say. Added 2026-09-04\n                # (external review, Round 8 H4).\n                #\n                # Arbitration was start-time-only, so a LEGACY aify-comms environment bridge -- one\n                # on a host that has not re-run install.sh -- took this row simply by starting later,\n                # and then became the only party `_claim_spawn_request_once` would let claim. Two\n                # spawners on one host is the collision the environment tier exists to end, and\n                # v0.6.2 deleting that cluster makes every surviving bridge old code nobody tracks.\n                #\n                # ABSENT MEANS LEGACY, so a fleet that has not upgraded behaves exactly as before,\n                # and so does an older aify-env against this service. When both sides are the same\n                # kind, this says nothing and the start-time rule below decides, unchanged.\n                incoming_is_host_tier = _is_host_tier(metadata)\n                existing_is_host_tier = _is_host_tier(existing_metadata)\n                if incoming_is_host_tier and not existing_is_host_tier:\n                    logger.info(\n                        "environment %s: host tier %s takes the row from legacy bridge %s "\n                        "(kind outranks start time)",\n                        env_id, incoming_bridge_id, existing_bridge_id,\n                    )\n                    superseded_bridge_id = existing_bridge_id\n                    existing_started = None\n                elif existing_is_host_tier and not incoming_is_host_tier:\n                    # REFUSED, and it says why in the words the reader needs: this is not a clock\n                    # problem and re-registering will not help. The bridge is the thing that should\n                    # not be running.\n                    return {\n                        "ok": True,\n                        "environment": _environment_record_to_dict(existing),\n                        "claimer": {\n                            "accepted": False,\n                            "bridgeId": existing_bridge_id,\n                            "reason": (\n                                "this environment is held by the aify-env host tier, which outranks "\n                                "an aify-comms environment bridge. That bridge is retired: re-run "\n                                "install.sh on this host and relaunch its wrappers."\n                            ),\n                        },\n                    }\n',
        '',
    ),
    (
        # The reader that entry depends on, module-level and therefore also declared.
        '#: What a host-tier claimer calls itself. `aify-env` sends this in `metadata.bridgeKind`; a legacy\n#: aify-comms environment bridge sends nothing, which is exactly what makes the absence meaningful.\nHOST_TIER_BRIDGE_KIND = "aify-env"\n\n\ndef _is_host_tier(metadata: Any) -> bool:\n    """Whether this beat comes from the HOST TIER rather than a legacy environment bridge.\n\n    ABSENT MEANS LEGACY, and that is the whole reason this reads a positive marker rather than a\n    version. Every pre-0.6.2 sender is silent here, so a missing value is a fact about the sender and\n    not a gap in the data.\n    """\n    if isinstance(metadata, dict):\n        return str(metadata.get("bridgeKind") or "").strip().lower() == HOST_TIER_BRIDGE_KIND\n    return False\n',
        '',
    ),
    (
        # OWNERSHIP AT THE BOUNDARY. Preservation restored a stored bridge key only when the caller
        # stayed SILENT about it -- `next_metadata` starts as a copy of the caller`s metadata, so a
        # caller that SENT `bridgeBuild` already had the key present and its forged value won. A beat
        # with no `bridgeId` now loses the whole `bridge*` namespace before the merge.
        "        #: What the CALLER advertised, which is a different fact from what the row now holds. A\n        #: caller that said nothing has not advertised an empty list, so the previous claim stands.\n        advertised_roots = cwd_roots if cwd_roots is not None else (\n            existing_metadata.get(\"advertisedCwdRoots\", []) if existing else []\n        )\n        #: OWNERSHIP IS ENFORCED AT THE BOUNDARY, not assumed from a naming convention.\n        #:\n        #: Deriving the preserved set by prefix decided WHICH keys are bridge-owned and said nothing\n        #: about who may WRITE them. Preservation below runs as \"restore the stored value if the key\n        #: is not already here\" — and `next_metadata` starts as a copy of the CALLER's metadata, so\n        #: a caller that SENT `bridgeBuild` already had the key present and its forged value won.\n        #:\n        #: What that bought a host advertiser was not cosmetic. `bridgeLastSeen` is the evidence the\n        #: spawn gate reads to decide a host has a live bridge, so forging it gets spawns accepted\n        #: that nothing can claim. `bridgeBuild` is what `bridge-current` compares against repo HEAD,\n        #: so forging it silences the one instrument that can say a bridge is running old code.\n        #:\n        #: Only a bridge sends a `bridgeId`. A caller without one has its whole `bridge*` namespace\n        #: dropped before the merge; everything else it sent is kept, so an advertisement is still an\n        #: advertisement. aify-env emits no such key by design, which a cross-repo test pins.\n        if not str(req.bridgeId or \"\").strip():\n            metadata = {\n                key: value for key, value in metadata.items()\n                if not (isinstance(key, str) and key.startswith(\"bridge\"))\n            }\n        next_metadata = {**metadata, \"advertisedCwdRoots\": advertised_roots}",
        "        #: What the CALLER advertised, which is a different fact from what the row now holds. A\n        #: caller that said nothing has not advertised an empty list, so the previous claim stands.\n        advertised_roots = cwd_roots if cwd_roots is not None else (\n            existing_metadata.get(\"advertisedCwdRoots\", []) if existing else []\n        )\n        next_metadata = {**metadata, \"advertisedCwdRoots\": advertised_roots}",
    ),
    (
        # ADDED, declared as a deletion: `bridgeLastSeen` separates 'this row was written' from
        # 'a bridge spoke', which stopped being the same fact when aify-env began heartbeating.
        '        # WHEN A BRIDGE LAST SPOKE, which is a different question from when this ROW was last written.\n        # Only a bridge sends a `bridgeId`, so only a bridge sets this; an advertisement from aify-env\n        # preserves it below rather than refreshing it. Without the split, a host with no bridge reads\n        # `online` off aify-env\'s beat and accepts spawns nothing can claim.\n        if str(req.bridgeId or "").strip():\n            next_metadata["bridgeLastSeen"] = now\n',
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
        '# it travelled with the drain that was its only reader.\n\n\n#: What only a BRIDGE can know about itself, and therefore what an advertisement must leave alone.\n#:\n#: An environment-tier heartbeat describes the HOST -- runtimes, roots, terminal availability -- and\n#: declares no `bridgeId`. Its metadata carries no `bridgeStartedAt` either, and `next_metadata`\n#: REPLACES the stored metadata, so an advertisement erased the timestamp the supersede arbitration\n#: reads. Preserving `bridge_id` alone was not enough: with two ids and no start times, the branch\n#: that refuses an OLDER incoming bridge cannot fire, and a stale bridge reclaims the environment a\n#: fresh one owns.\n#:\n#: DERIVED FROM ITS READERS, not guessed: `bridgeStartedAt` is read by `_bridge_started_at` here and\n#: by `environment_claim.py`, and nothing else in the service reads a `bridge*` metadata key. A\n#: second one belongs in this tuple the day it gets a reader.\n#: The metadata keys only a BRIDGE can answer for, DERIVED by prefix rather than listed.\n#:\n#: A list of two was here, and it was one short: `bridgeBuild` rides in the same blob, `next_metadata`\n#: replaces the blob, and aify-env\'s advertisement beats every 30s -- so a bridge\'s reported build was\n#: erased within half a minute of being written, and `bridge-current` went back to reporting no\n#: evidence. That check exists to answer "is a running bridge executing old code", which nothing else\n#: can, and the cutover silently disabled it.\n#:\n#: `bridge*` is the honest rule: aify-env is not a bridge and sends no key by that name, so a prefix\n#: match cannot over-claim, and a key added to the bridge\'s payload later is covered without anyone\n#: remembering this line. `BRIDGE_OWNED_METADATA` stays as the explicit floor for the two that must be\n#: preserved even if the prefix convention is ever broken.\nBRIDGE_OWNED_METADATA = ("bridgeStartedAt", "bridgeLastSeen", "bridgeBuild")\n\n\ndef _bridge_owned_metadata_keys(existing_metadata) -> tuple:\n    """Every key in the stored metadata that only a bridge could have written.\n\n    Prefix-derived, with the named floor unioned in so a rename upstream cannot silently drop one.\n    """\n    stored = tuple(\n        key for key in (existing_metadata or {})\n        if isinstance(key, str) and key.startswith("bridge")\n    )\n    return tuple(dict.fromkeys(BRIDGE_OWNED_METADATA + stored))',
        '# it travelled with the drain that was its only reader.\n',
    ),
    (
        '        if not str(req.bridgeId or "").strip():\n            for bridge_key in _bridge_owned_metadata_keys(existing_metadata):\n                if bridge_key in existing_metadata and bridge_key not in next_metadata:\n                    next_metadata[bridge_key] = existing_metadata[bridge_key]\n        # And the host\'s own answers, for a caller that described no host. Keyed on the request field\n        # being absent: a caller that sent `terminal: false` is making a claim and is believed.\n        for request_field, metadata_key in HOST_OWNED_METADATA:\n            if getattr(req, request_field, None) is None and metadata_key in existing_metadata:\n                next_metadata.setdefault(metadata_key, existing_metadata[metadata_key])\n        if manual_roots:',
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
    (
        # A REFUSED HEARTBEAT NOW SAYS SO. `ok: True` alone is what an ACCEPTED beat returns, so a
        # bridge whose beat was arbitrated away could not tell -- it kept beating, `bridgeLastSeen`
        # never moved, `/spawn` refused everything, and both sides reported healthy for hours.
        "                    # REFUSED, AND IT SAYS SO. `ok: True` alone is what a heartbeat that was\n                    # ACCEPTED returns, so a bridge whose beat was arbitrated away could not tell\n                    # the difference -- it kept beating every 30s, believing it was the claimer,\n                    # while `bridgeLastSeen` never moved and `/spawn` refused every request. That\n                    # is the shape this repo has fixed three times elsewhere (\"no evidence is not a\n                    # pass\"), and on 2026-09-02 it cost a day here: a plugin sending its start time\n                    # in the wrong place landed on this branch forever and reported healthy.\n                    #\n                    # `ok` STAYS TRUE: the request was well-formed and the row is fine. What is\n                    # added is WHO the claimer is, so a caller can compare it with its own id.\n                    return {\n                        \"ok\": True,\n                        \"environment\": _environment_record_to_dict(existing),\n                        \"claimer\": {\n                            \"accepted\": False,\n                            \"bridgeId\": existing_bridge_id,\n                            \"reason\": (\n                                \"an existing bridge started later than this one, or this beat \"\n                                \"carried no metadata.bridgeStartedAt to arbitrate on\"\n                            ),\n                        },\n                    }",
        "                    return {\"ok\": True, \"environment\": _environment_record_to_dict(existing)}",
    ),
    (
        # The same silence on the other refusal: a bridge going offline must not take down a row a
        # different bridge now owns. Correct behaviour, previously indistinguishable from a write.
        "            # The same silence, on the other refusal: a bridge saying it is going offline must not\n            # take down a row a DIFFERENT bridge now owns. Correct, and previously indistinguishable\n            # from having been recorded.\n            return {\n                \"ok\": True,\n                \"environment\": _environment_record_to_dict(existing),\n                \"claimer\": {\n                    \"accepted\": False,\n                    \"bridgeId\": str(existing[\"bridge_id\"] or \"\").strip(),\n                    \"reason\": \"another bridge owns this row, and this beat was not an online one\",\n                },\n            }",
        "            return {\"ok\": True, \"environment\": _environment_record_to_dict(existing)}",
    ),
    (
        # And the ACCEPTED case, which the pair needs: `accepted: False` is only legible against an
        # `accepted: True` a caller can also see, or a missing field has to be read as success.
        "        # THE ACCEPTED CASE SAYS SO TOO, and it has to: `accepted: False` is only legible against an\n        # `accepted: True` that a caller can also see. Without this pair, a caller could not tell a\n        # refusal from a service too old to answer the question -- and \"the field is missing\" would\n        # have to be read as success, which is how the silence got here in the first place.\n        #\n        # `bridgeId` is echoed rather than assumed: a beat with no bridgeId is an ADVERTISEMENT, not\n        # a claim, and reporting it as an accepted claimer would invent the very authority the\n        # advertise/claim split exists to withhold.\n        claimer_id = str(req.bridgeId or \"\").strip()\n        return {\n            \"ok\": True,\n            \"environment\": environment,\n            \"claimer\": {\n                \"accepted\": bool(claimer_id),\n                \"bridgeId\": claimer_id,\n                \"reason\": \"\" if claimer_id else \"no bridgeId: this beat describes the host, it does not claim work\",\n            },\n        }",
        "        return {\"ok\": True, \"environment\": environment}",
    ),
    (
        # A DEAD INCUMBENT HAS NO STANDING. Arbitration compared start times and never asked
        # whether the holder was alive, so a bridge that started later and then exited held the row
        # for ever -- measured 2026-09-03, twenty minutes of refused beats while `/spawn` told the
        # operator to start a claimer that was already running. Declared as a deletion because it
        # is an ADDITION: the reconstruction removes it to recover the fixture.
        "                # A DEAD INCUMBENT HAS NO STANDING, and this is the half that was missing.\n                #\n                # Arbitration compared START TIMES and never asked whether the holder was alive, so a\n                # bridge that started later and then EXITED held the row for ever: nothing older\n                # could take it, and only something started even later could. Measured 2026-09-03 --\n                # a restarted aify-env claimed the row, exited, and the surviving daemon beat every\n                # 30 seconds for 20 minutes and was refused every time, while `/spawn` told the\n                # operator to start a claimer that was already running.\n                #\n                # `bridgeLastSeen` is the liveness fact this service already keeps and already gates\n                # `/spawn` on. Arbitration ignoring it was the inconsistency: the endpoint would not\n                # have let the incumbent claim anything either. Stale means beyond the SAME window,\n                # so the two answers cannot disagree.\n                #\n                # This can only ever ADMIT a live beat, never refuse one -- the branch below is\n                # skipped, not extended -- and by the time it applies, the incumbent has been silent\n                # longer than any spawn would have waited for it.\n                incumbent_is_live = _environment_has_live_bridge(\n                    {\"metadata\": existing_metadata}, bridge_rows_say_live=None,\n                )\n                if existing_started and not incumbent_is_live:\n                    logger.info(\n                        \"environment %s: bridge %s started later but has been silent since %s; \"\n                        \"handing the row to live bridge %s\",\n                        env_id, existing_bridge_id,\n                        existing_metadata.get(\"bridgeLastSeen\") or \"never\", incoming_bridge_id,\n                    )\n                    existing_started = None\n",
        "",
    ),
]

#: DECLARED ADDITION, 2026-09-04 (external review, Round 8 M1). A `bridgeStartedAt` in the
#: future outranked every correctly clocked bridge until real time caught up with it, so it is
#: bounded once on the way in. Declared as a deletion, so the pre-split baseline survives.
#:
#: ONE ENTRY, NOT FIVE, and finding that out is the useful part. My first attempt declared the
#: H4 arbitration rule, the host-tier reader and two import lines as well, and the gate answered
#: "0 occurrences" -- because these pairs are applied IN ORDER and an earlier one already
#: consumes the region the H4 rule sits in, while module-level additions never appear in the
#: FUNCTION this gate compares at all. Inserting a pair at the TOP of the list is worse still:
#: it changes what every later pair sees, and the reconstruction lost a 112-line block.
#:
#: So a declaration is written against the source as it stands AFTER the existing edits, and
#: appended. The block below was extracted by diffing that text against the fixture rather than
#: transcribed -- this repo's rule about exact-match strings, applied to the tool that enforces
#: exact-match strings.
EDITED_SINCE = EDITED_SINCE + [
    ('    # A START TIME IN THE FUTURE IS BOUNDED ON THE WAY IN, once, against the clock as it is now.\n    # External review, Round 8 M1: arbitration prefers the LATER start time and nothing bounded how\n    # late, so a value in the future outranked every correctly clocked bridge until real time caught\n    # up with it. Bounding it at READ time was tried and is worse -- see `_bridge_started_at`.\n    if isinstance(metadata, dict) and metadata.get("bridgeStartedAt"):\n        _claimed_start = _parsed_timestamp(metadata.get("bridgeStartedAt"))\n        # PAST THE SKEW TOLERANCE, not past `now`. This clamped at exactly now until a test caught\n        # it, and a zero-tolerance future check is a shape this repo has already paid for: doctor\'s\n        # `env-bridge` read a container clock 4.1 seconds ahead of its host and reported every fresh\n        # heartbeat as bogus. An ordinary host a few seconds out is not making a claim about the\n        # future; it is a host with a clock.\n        #\n        # `BRIDGE_STAMP_SKEW_TOLERANCE_SECONDS` is the number this service already uses for exactly\n        # this question in `env_status.py`, so the two answers cannot drift apart. A second constant\n        # here would be a rule to keep in step, which is a defect with a delay on it.\n        _ceiling = _parsed_timestamp(\n            (datetime.now(timezone.utc)\n             + timedelta(seconds=BRIDGE_STAMP_SKEW_TOLERANCE_SECONDS)).strftime(ISO_SECONDS)\n        )\n        if _claimed_start and _ceiling and _claimed_start > _ceiling:\n            logger.info(\n                "environment %s: bridge %s reported starting at %s, which is in the future; "\n                "recording %s instead. Check that host\'s clock.",\n                env_id, str(req.bridgeId or "") or "(none)", metadata.get("bridgeStartedAt"), _ceiling,\n            )\n            metadata = {**metadata, "bridgeStartedAt": _ceiling}\n', ''),
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
