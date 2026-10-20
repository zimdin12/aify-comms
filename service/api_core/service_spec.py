"""The registration spec this service serves at `/.well-known/aify-service` (guided install S1).

`scripts/stamp.sh` writes `service/_service_spec.json` as `{buildSha, spec}` beside the build stamp
(`mcp/stdio/service-spec-cli.mjs` builds it from the commit). This module decides whether that file may
be served, and refuses rather than serving a spec that is stale, unversioned, or breaks the format's
rules. A refused spec is a 503: a new host that cannot learn this service must not learn a wrong one.

Pure: text and the running build in, the document or the reason out. It reads no credential and no
environment, so nothing it serves can carry one.
"""
from __future__ import annotations

import json

SPEC_VERSION = 1
#: Fields a connect derives itself (endpoint, mcp) or takes from `secrets` (credential references).
_DERIVED_FIELDS = frozenset({"endpoint", "mcp"})


def spec_problems(spec) -> list[str]:
    """The format owner's rules for a served document, each violation named (S1, "The concrete document")."""
    if not isinstance(spec, dict):
        return ["the spec is not an object"]
    problems = []
    if spec.get("specVersion") != SPEC_VERSION:
        problems.append(f"specVersion is {spec.get('specVersion')!r}, not {SPEC_VERSION}")
    fixed = spec.get("fixed")
    if not isinstance(fixed, dict):
        problems.append("fixed is not an object")
        fixed = {}
    for field in fixed:
        if field in _DERIVED_FIELDS or field.lower().endswith("credentialref"):
            problems.append(f"fixed holds {field!r}, which a connect derives or secrets own")
    probes = ((spec.get("verify") or {}).get("probes")) or {}
    for name, probe in probes.items():
        if not (isinstance(probe, dict) and probe.get("path") and probe.get("header")):
            problems.append(f"probe {name!r} does not name its path and header")
    for secret in spec.get("secrets") or []:
        probe = secret.get("probe") if isinstance(secret, dict) else None
        if probe is not None and probe not in probes:
            problems.append(f"secret {secret.get('field')!r} names probe {probe!r}, which verify.probes lacks")
    for artifact in spec.get("artifacts") or []:
        source = artifact.get("source") or {}
        if "pending" in source and ("sha256" in artifact or set(source) != {"pending"}):
            problems.append(f"artifact {artifact.get('id')!r} is pending yet carries a hash, revision or URL")
    return problems


def served_spec(text: str | None, build_sha: str):
    """`(spec, "")` when the stamped file may be served for this build, else `(None, reason)`."""
    if text is None:
        return None, "this build was stamped without a service spec"
    try:
        stamped = json.loads(text)
    except ValueError:
        return None, "the stamped service spec is not JSON"
    if not isinstance(stamped, dict):
        return None, "the stamped service spec is not an object"
    if not build_sha or build_sha == "unknown" or stamped.get("buildSha") != build_sha:
        return None, "the stamped service spec was built for another build"
    spec = stamped.get("spec")
    problems = spec_problems(spec)
    if problems:
        return None, "the stamped service spec breaks the format: " + "; ".join(problems)
    return spec, ""
