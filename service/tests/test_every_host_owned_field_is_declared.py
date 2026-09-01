"""A host-owned field the declaration does not name is a field the next advertisement erases.

WHY THIS GATE EXISTS. `HOST_OWNED_METADATA` was a hand-kept tuple of three with nothing checking it.
The two tests that mentioned it did not test it: one embedded it in a source-text fixture asserting an
extraction had stayed byte-identical, the other named it in a docstring about query count. So the set
could fall behind the model and nothing would go red.

That is not hypothetical here. The erasure has already happened twice. `50a61dbe` stopped a heartbeat
blanking fields it did not mention; `badab14c` then found FOUR MORE it had missed. A third round is
what this file is for.

THE SHAPE OF THE CHECK. A field is host-describing when the handler treats its ABSENCE as "the caller
said nothing about the host" -- written as `if req.X is not None` in the heartbeat handler. That guard
is the ground truth, because it is the code that actually decides. So:

    forward   every declared field must be guarded that way in the handler
    backward  every field guarded that way must be declared

Either direction failing is a real divergence: a declaration naming a field the handler no longer
guards is stale, and a guard the declaration does not name is the erasure this file exists to stop.

DERIVED FROM SOURCE, NOT FROM A SECOND LIST. Repeating the field names here would just move the
hand-maintained list into the test and re-create the defect one file over -- this repo already learned
that with `sql_sources.py` and `doctor-sources.mjs`.
"""

from __future__ import annotations

import re
from pathlib import Path

from service.models import EnvironmentHeartbeat
from service.routers.environments import (
    COLUMN_CARRIER,
    HOST_OWNED_FIELDS,
    HOST_OWNED_METADATA,
    METADATA_CARRIER,
)

HANDLER = Path(__file__).resolve().parents[1] / "routers" / "environments.py"

#: The guard that marks a field as host-describing: absence means "said nothing about the host".
GUARD = re.compile(r"if\s+req\.([A-Za-z_][A-Za-z0-9_]*)\s+is\s+None\b|"
                   r"if\s+req\.([A-Za-z_][A-Za-z0-9_]*)\s+is\s+not\s+None\b")


def _code_lines(source: str) -> str:
    """The handler with its comments removed.

    THE SCANNER READ ITS OWN DOCUMENTATION on the first run and reported a field called `X`, from a
    comment in this very file describing the pattern it looks for. A scanner that cannot tell code
    from prose about code will confirm whatever the prose says -- and my positive control did not
    catch it, because it asked whether a real field was FOUND, never whether a fake one was excluded.
    """
    return "\n".join(line.split("#", 1)[0] for line in source.splitlines())


def _guarded_fields() -> set[str]:
    """Every `req.X` the handler tests for None-ness, which is how it says "the caller may omit this"."""
    source = _code_lines(HANDLER.read_text(encoding="utf-8"))
    found: set[str] = set()
    for match in GUARD.finditer(source):
        found.add(match.group(1) or match.group(2))
    return found


def test_the_guard_scanner_finds_something() -> None:
    """POSITIVE CONTROL. A scanner that matched nothing would make every assertion below vacuous.

    This is the failure mode that produced the defect in the first place: a check that cannot fail
    reads exactly like a check that passes.
    """
    guarded = _guarded_fields()
    assert guarded, "the guard scanner found no `if req.X is None` at all -- it is broken, not clean"
    assert "runtimes" in guarded, "the scanner missed a guard that is definitely in the handler"


def test_the_scanner_does_not_match_a_field_that_is_absent() -> None:
    """NEGATIVE CONTROL. A scanner that matched everything would also pass every assertion."""
    assert "zzNoSuchField" not in _guarded_fields()


def test_the_scanner_ignores_comments() -> None:
    """It did not, on its first run, and reported a field named `X` out of its own documentation.

    A scanner that reads prose about code confirms whatever the prose claims. The original positive
    control asked whether a real field was found and never whether a fake one was excluded, which is
    the same asymmetry that lets a negative assertion pass while proving nothing.
    """
    assert _code_lines("code_here\n# if req.Fake is not None:\nmore = 1") == "code_here\n\nmore = 1"
    assert "Fake" not in _guarded_fields()


def test_every_declared_host_field_is_guarded_in_the_handler() -> None:
    """Forward: a declaration naming a field the handler no longer guards is stale."""
    guarded = _guarded_fields()
    declared = {field for field, _carrier, _key in HOST_OWNED_FIELDS}
    stale = sorted(declared - guarded)
    assert stale == [], (
        f"declared as host-owned but not guarded in the handler: {stale}. "
        "Either the handler stopped treating absence as 'said nothing', or the declaration is stale."
    )


def test_every_guarded_host_field_is_declared() -> None:
    """Backward: a guarded field nobody declared is erased on the next advertisement.

    This is the direction that catches the defect. `badab14c` exists because four such fields were
    added and preserved by nothing.
    """
    guarded = _guarded_fields()
    declared = {field for field, _carrier, _key in HOST_OWNED_FIELDS}
    # `metadata` and `status` are guarded but are not host FACTS: metadata is the carrier itself and
    # status is a caller's claim about the environment rather than a description of the host.
    carriers_not_facts = {"metadata", "status"}
    undeclared = sorted(guarded - declared - carriers_not_facts)
    assert undeclared == [], (
        f"guarded as omittable but never declared host-owned: {undeclared}. "
        "A field the caller may omit and nothing preserves is blanked on the next advertisement."
    )


def test_every_declared_field_exists_on_the_model() -> None:
    """A declaration naming a field the request cannot carry is a typo with a delay on it."""
    fields = set(EnvironmentHeartbeat.model_fields)
    missing = sorted(field for field, _c, _k in HOST_OWNED_FIELDS if field not in fields)
    assert missing == [], f"declared host-owned but not a field of EnvironmentHeartbeat: {missing}"


def test_both_carriers_are_represented() -> None:
    """The defect was a metadata-only view of a two-carrier problem.

    Three of these live in the `metadata` blob and TWO are COLUMNS. A set that named only the metadata
    keys looked complete while describing three fifths of it, which is exactly why the column half
    could go unmentioned for as long as it did.

    THIS FILE CHECKS MEMBERSHIP ONLY, and that boundary is worth stating because it was not obvious:
    review changed one storage key to `terminalBROKEN` and every test here still passed while the live
    route erased the stored value on omission. Naming the right members says nothing about what they
    map to. `test_each_declared_host_field_survives_omission.py` drives the real route to check that.
    """
    carriers = {carrier for _f, carrier, _k in HOST_OWNED_FIELDS}
    assert carriers == {METADATA_CARRIER, COLUMN_CARRIER}, (
        f"expected both carriers to be declared, got {sorted(carriers)}"
    )


def test_the_metadata_half_is_derived_not_repeated() -> None:
    """`HOST_OWNED_METADATA` must be a projection, or the two lists drift apart."""
    expected = tuple(
        (field, key) for field, carrier, key in HOST_OWNED_FIELDS if carrier == METADATA_CARRIER
    )
    assert HOST_OWNED_METADATA == expected
    assert all(carrier == METADATA_CARRIER
               for _f, carrier, _k in HOST_OWNED_FIELDS
               if (_f, _k) in HOST_OWNED_METADATA)
