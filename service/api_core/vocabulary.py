"""The single Python owner of the cross-language vocabulary.

v0.5.1d. Loads `service/contracts/vocabulary.json`, which is the contract described in finding H1 of
the v0.2 review: *"the system's core vocabulary has no single home — it is hand-copied across the
language boundary."*

This module is the ONLY place Python declares those words. Every Python reader imports from here,
so there is no second Python copy to drift. (This used to name the reader, first `api_v2.py` and then
the control-plane module; both names went stale when those files stopped importing from here.) The JS bridge keeps its own literal map — it has to, because
`install.sh` copies only `mcp/stdio/` to `~/.aify-comms` and a file under `service/` does not exist
on that host — and an agreement test in each suite fails if the two ever disagree.

A leaf: standard library only, no service imports, so it can never join an import cycle.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import MappingProxyType

CONTRACT_PATH = Path(__file__).resolve().parent.parent / "contracts" / "vocabulary.json"


def _load() -> dict:
    with open(CONTRACT_PATH, encoding="utf-8") as handle:
        return json.load(handle)


_CONTRACT = _load()

#: Read-only so a caller cannot mutate the vocabulary for everyone else in-process. The service is
#: deliberately single-worker with process-global caches, which means one careless `.update()` would
#: be visible to every subsequent request rather than to one caller.
RUNTIME_ALIASES = MappingProxyType(dict(_CONTRACT["runtimes"]["aliases"]))
CANONICAL_RUNTIMES = frozenset(_CONTRACT["runtimes"]["canonical"])
LAUNCHABLE_RUNTIMES = frozenset(_CONTRACT["runtimes"]["launchable"])

#: Runtimes whose dispatch runs sit `running` until a turn ends, rather than closing at `delivered`.
#:
#: A DIFFERENT QUESTION FROM LAUNCHABILITY, holding the same members today. "Can the service start a
#: worker for this?" and "does a run against this stay open until the turn finishes?" are independent
#: properties that happen to coincide, and `console_input_queue` names the second one itself:
#: `tracks_active_turn`.
#:
#: DECLARED HERE RATHER THAN DERIVED, because the runtime contract does not model it -- the contract
#: knows which runtimes are launchable and says nothing about turn tracking. A list you must remember
#: to update is a defect with a delay on it, so this one is at least named, in the file that owns
#: runtime vocabulary, instead of being spelled out at each of its two call sites.
#:
#: DO NOT COLLAPSE THIS INTO `LAUNCHABLE_RUNTIMES` on the grounds that the sets are equal. They are
#: equal by coincidence, and the moment a runtime is launchable without tracking turns -- or the
#: reverse -- one call site would silently start answering the other question. If they diverge, this
#: is the one that changes and the contract is the one that does not.
RUNTIMES_THAT_TRACK_A_TURN = frozenset({"claude-code", "codex", "hermes", "opencode", "pi"})
DEFINITION_STATES = frozenset(_CONTRACT["definitionStates"]["values"])
SPAWN_STATES = frozenset(_CONTRACT["spawnStates"]["values"])
SESSION_MODES = frozenset(_CONTRACT["sessionModes"]["values"])
SELF_REPORTED_STATUSES = tuple(_CONTRACT["selfReportedStatuses"]["values"])
AGENT_STATUSES = tuple(_CONTRACT["agentStatuses"]["values"])
AGENT_STATUS_MEANINGS = MappingProxyType(dict(_CONTRACT["agentStatuses"]["meanings"]))


# Exact export: aify-env a03b88ec7bc5a077ef299609b5f2ea8bf225dbe7:lib/harness-facts-v1.json.
_HARNESS_FACTS_PATH = CONTRACT_PATH.with_name("harness-facts-v1.json")
_HARNESS_FACTS_SHA256 = "dabd9e5c9c71107c58310d7a73c26512b7616822614045021ebbf98edcb73d9f"


def _definition_harnesses(facts: object) -> dict[str, str]:
    if not isinstance(facts, dict) or type(facts.get("schemaVersion")) is not int or facts["schemaVersion"] != 1:
        raise RuntimeError("Env harness facts: unsupported schema")
    table = facts.get("harnesses")
    if not isinstance(table, dict):
        raise RuntimeError("Env harness facts: invalid harness table")
    admitted = {}
    for name, row in table.items():
        if (not name or not isinstance(row, dict) or type(row.get("definition")) is not bool
                or not isinstance(row.get("runtime"), str) or not row["runtime"]):
            raise RuntimeError(f"Env harness facts: invalid harness row {name!r}")
        if row["definition"]:
            admitted[name] = row["runtime"]
    if not admitted:
        raise RuntimeError("Env harness facts: no definition runtimes")
    if len(set(admitted.values())) != len(admitted):
        raise RuntimeError("Env harness facts: duplicate definition runtime")
    return admitted


def _load_definition_harnesses(path: Path, expected_sha256: str) -> dict[str, str]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise RuntimeError(f"cannot read Env harness facts at {path}") from exc
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise RuntimeError(f"Env harness facts SHA-256 mismatch at {path}")
    try:
        facts = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid Env harness facts JSON at {path}") from exc
    return _definition_harnesses(facts)


#: Definition admission comes from Env. This does not set Comms' broader runtime or alias vocabulary.
HARNESS_RUNTIME = _load_definition_harnesses(_HARNESS_FACTS_PATH, _HARNESS_FACTS_SHA256)
RUNTIME_HARNESS = {runtime: harness for harness, runtime in HARNESS_RUNTIME.items()}
