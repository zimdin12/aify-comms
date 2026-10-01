"""A host's agent-definition snapshot, as the service judges it (P0 C3). PURE: no database, no clock.

aify-env pushes this machine's complete snapshot; the service checks it is well formed, that its
digest is the one the host computed, that it comes from the environment's current claimer on that
machine, and where it falls in the order of that machine's stores. What the push then changes is
`definition_push.py`.

THE CANONICAL FORM IS SHARED WITH aify-env, byte for byte (its `agent-definition-snapshot.mjs`), and
the golden vectors in its tests/fixtures/agent-definitions/cases.json hold both sides to it: entries
sorted by id in UTF-16 code-unit order, each in its digested form, keys sorted at every depth, no
whitespace, UTF-8, with strings escaped as `JSON.stringify` and `json.dumps(ensure_ascii=False)` both
escape them.
"""
from __future__ import annotations

import hashlib
import json
import re
from enum import Enum
from typing import Any, Optional

#: Why a valid definition cannot start on its host, as the host words it.
UNAVAILABLE_HARNESS = "harness-not-installed"

_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def canonical(value: Any) -> str:
    # A LONE SURROGATE is escaped, lower case, as JavaScript's well-formed `JSON.stringify` escapes it.
    # `json.loads` pairs valid surrogates into one code point, so any left in a str are lone; left raw,
    # they make the UTF-8 encoding of the digest fail where the host's succeeds.
    text = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _LONE_SURROGATE.sub(lambda match: "\\u%04x" % ord(match.group()), text)


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def definition_digest(agent: dict) -> str:
    """The digest of a definition's `agent` object, as the host computes it."""
    return sha256_hex(canonical(agent))


def _utf16_order(text: str) -> bytes:
    # JavaScript compares strings by UTF-16 code unit; Python's str order is by code point, and the
    # two differ once an astral character meets one from U+E000-U+FFFF (an invalid entry's id is its
    # filename and can hold either).
    return text.encode("utf-16-be", "surrogatepass")


def _digested(entry: dict) -> dict:
    if entry["state"] == "invalid":
        return {"id": entry["id"], "state": "invalid", "problems": sorted(entry["problems"])}
    digested = {
        "id": entry["id"], "state": "valid", "incarnation": entry["incarnation"], "revision": entry["revision"],
        "definitionDigest": entry["definitionDigest"], "available": entry["available"],
    }
    if not entry["available"]:
        digested["unavailableReason"] = entry["unavailableReason"]
    return digested


def snapshot_digest(entries: list[dict]) -> str:
    """The digest of a snapshot's entries: what the host sends as `snapshotDigest`."""
    ordered = sorted(entries, key=lambda entry: _utf16_order(entry["id"]))
    return sha256_hex(canonical([_digested(entry) for entry in ordered]))


def is_counter(value: Any) -> bool:
    """A store revision, incarnation or entry revision: a whole number from 1, never a boolean."""
    return isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 2 ** 53 - 1


def entry_problems(entry: Any) -> list[str]:
    """What is wrong with one pushed entry's SHAPE, or [] when it can be applied.

    The host has already judged the definition against C1; the service checks only what it relies on,
    and that a valid entry's definition is the one its digest names, so a push cannot carry a body that
    disagrees with the digest the snapshot was ordered by.
    """
    if not isinstance(entry, dict):
        return ["entry: not an object"]
    if not isinstance(entry.get("id"), str):
        return ["id: not a string"]
    state = entry.get("state")
    if state == "invalid":
        problems = entry.get("problems")
        if not isinstance(problems, list) or not problems or not all(isinstance(p, str) for p in problems):
            return [f"{entry['id']}: problems must be a non-empty list of strings"]
        return []
    if state != "valid":
        return [f"{entry['id']}: state must be valid or invalid"]
    found = []
    for counter in ("incarnation", "revision"):
        if not is_counter(entry.get(counter)):
            found.append(f"{entry['id']}: {counter} must be a whole number from 1")
    if not isinstance(entry.get("available"), bool):
        found.append(f"{entry['id']}: available must be true or false")
    elif not entry["available"] and entry.get("unavailableReason") != UNAVAILABLE_HARNESS:
        found.append(f"{entry['id']}: an unavailable entry says why ({UNAVAILABLE_HARNESS})")
    definition = entry.get("definition")
    if not isinstance(definition, dict) or definition.get("id") != entry["id"]:
        found.append(f"{entry['id']}: definition must be an object naming the same id")
    elif definition_digest(definition) != entry["definitionDigest"]:
        found.append(f"{entry['id']}: definitionDigest does not match the definition")
    return found


def snapshot_problems(entries: Any, snapshot_digest_claimed: Any) -> list[str]:
    """Why a pushed snapshot cannot be applied at all, or []: its entries, their ids, and its digest."""
    if not isinstance(entries, list):
        return ["entries: not a list"]
    found = [problem for entry in entries for problem in entry_problems(entry)]
    if found:
        return found
    ids = [entry["id"] for entry in entries]
    if len(set(ids)) != len(ids):
        return ["entries: an id appears twice"]
    if snapshot_digest(entries) != snapshot_digest_claimed:
        return ["snapshotDigest: does not match the entries"]
    return []


def fence_refusal(environment: Optional[dict], bridge_id: str, machine_id: str) -> str:
    """Why this push may not speak for the environment's machine, or "".

    FAILS CLOSED. The spawn claim lets anyone through while an environment has no recorded claimer; a
    definition push may not, because it can withdraw every agent the machine owns.
    """
    if not environment:
        return "no such environment"
    current = str(environment.get("bridge_id") or "").strip()
    if not current:
        return "this environment has no accepted claimer yet; beat first"
    if current != str(bridge_id or "").strip():
        return f"not the current claimer of this environment (that is {current})"
    if str(environment.get("machine_id") or "").strip() != str(machine_id or "").strip():
        return f"machineId does not match this environment's machine ({environment.get('machine_id') or ''})"
    return ""


class PushOrder(str, Enum):
    """Where an incoming push falls against its machine's stores (P0 C3, the ordering table)."""
    APPLY = "apply"            # becomes or advances the current store
    REPLAY = "replay"          # the current state again: 200, nothing changes
    CONFLICT = "conflict"      # same store and revision, another digest: a store bug or a forgery
    STALE = "stale"            # an older revision of the current store
    RETIRED = "retired"        # a store this machine has moved on from, for good
    NEW_STORE = "new-store"    # a store never seen here: applied, and the current one is retired


def push_order(current: Optional[dict], retired: set[str], store_id: str, revision: int, digest: str) -> PushOrder:
    """The ordering table, as a pure function of what the service holds for the machine."""
    if store_id in retired:
        return PushOrder.RETIRED
    if not current:
        return PushOrder.APPLY
    if store_id != current["store_id"]:
        return PushOrder.NEW_STORE
    if revision > current["revision"]:
        return PushOrder.APPLY
    if revision < current["revision"]:
        return PushOrder.STALE
    return PushOrder.REPLAY if digest == current["snapshot_digest"] else PushOrder.CONFLICT
