"""machineId is lowercased at ingress, on every request model that carries one.

The host machine id is `<platform>:<hostname>` — `win32:DevBox-1` — and the service compares it
CASE-SENSITIVELY in bridge supersession and dispatch-claim routing. Different launch paths report the
hostname with different casing, so without a single normalisation point the same machine arrives as
two, and the consequences are the quiet kind: a bridge fails to supersede its predecessor because the
ids do not match, or a claim is routed to a machine that looks different from the one that offered it.

Nothing named `_normalize_machine_id_value`, and nothing checked that the seven request models which
inherit the normalising base actually get it. That second half is the real risk: the validator is
attached by INHERITANCE with `check_fields=False`, so a model that carries a machineId and forgets to
extend the base is silently unnormalised — no error, no missing field, just a value that will not
compare equal later. This file enumerates the models from the module itself rather than by hand, so a
new one is caught by the same assertion.

`None` passes through as `None` on purpose: "unset" must stay distinct from "empty", because a
request that omits machineId means something different from one that sends "".
"""
from __future__ import annotations

import inspect

import pytest

from service import models
from service.models import _MachineIdNormalizingModel, _normalize_machine_id_value


# ── the rule itself ──────────────────────────────────────────────────────────────────────────
def test_the_host_casing_is_lowered_and_the_platform_is_untouched():
    assert _normalize_machine_id_value("win32:DevBox-1") == "win32:devbox-1"
    assert _normalize_machine_id_value("WIN32:DEVBOX-1") == "win32:devbox-1"
    assert _normalize_machine_id_value("linux:Laputa") == "linux:laputa"


def test_surrounding_whitespace_is_stripped():
    """A trailing newline from a shell `$(hostname)` capture is the commonest way this arrives dirty."""
    assert _normalize_machine_id_value("  win32:DevBox-1\n") == "win32:devbox-1"
    assert _normalize_machine_id_value("\twsl-ubuntu:HOST ") == "wsl-ubuntu:host"


def test_none_survives_as_none():
    """Unset and empty mean different things: one request omitted the field, the other sent nothing."""
    assert _normalize_machine_id_value(None) is None
    assert _normalize_machine_id_value("") == ""
    assert _normalize_machine_id_value("   ") == "", "whitespace-only collapses to empty, not to None"


def test_it_is_idempotent():
    """It runs at ingress and the value is stored; re-normalising a stored value must not move it."""
    once = _normalize_machine_id_value("  WIN32:DevBox-1  ")
    assert _normalize_machine_id_value(once) == once


def test_a_non_string_is_coerced_rather_than_raising():
    """This is a request-parse ingress — a client can send anything, and a 500 from a type error is
    a worse answer than a value that simply will not match."""
    assert _normalize_machine_id_value(12345) == "12345"
    assert _normalize_machine_id_value(True) == "true"


def test_two_spellings_of_one_machine_compare_equal_after_normalisation():
    """The whole point, stated as the property that matters: supersession and claim routing compare
    these strings directly."""
    assert _normalize_machine_id_value("win32:DevBox-1") == _normalize_machine_id_value("WIN32:devbox-1  ")


# ── every model that carries a machineId must inherit the normaliser ─────────────────────────
def _models_with_machine_id():
    """Enumerated from the module, not listed by hand, so a NEW model joins this test automatically."""
    found = []
    for name, obj in vars(models).items():
        if not inspect.isclass(obj) or not issubclass(obj, models.BaseModel):
            continue
        if obj is models.BaseModel or obj is _MachineIdNormalizingModel:
            continue
        if "machineId" in getattr(obj, "model_fields", {}):
            found.append((name, obj))
    return found


def test_the_enumeration_finds_a_real_population():
    """An empty list would make the test below pass while checking nothing."""
    found = _models_with_machine_id()
    assert len(found) >= 7, f"only {len(found)} models carry a machineId — the enumeration is broken"
    names = {name for name, _ in found}
    assert {"AgentRegister", "DispatchClaimRequest", "EnvironmentHeartbeat"} <= names


@pytest.mark.parametrize("name,model", _models_with_machine_id(), ids=lambda v: v if isinstance(v, str) else "")
def test_every_model_carrying_a_machine_id_normalises_it(name, model):
    """THE INHERITANCE IS THE CONTRACT. The validator is attached to the base with
    `check_fields=False`, so a model that declares machineId without extending
    `_MachineIdNormalizingModel` is silently unnormalised — no error, no missing field, just a value
    that will not compare equal to the same machine spelled differently."""
    assert issubclass(model, _MachineIdNormalizingModel), (
        f"{name} carries a machineId but does not extend _MachineIdNormalizingModel, so its value "
        f"reaches supersession and claim routing with whatever casing the client sent"
    )


def test_the_validator_actually_fires_through_a_real_model():
    """Inheritance is necessary but the assertion above does not prove the validator RUNS — a
    `check_fields=False` validator naming a field the model does not have is a silent no-op."""
    registered = models.AgentRegister(agentId="a", role="coder", machineId="  WIN32:DevBox-1 ")
    assert registered.machineId == "win32:devbox-1"

    claim = models.DispatchClaimRequest(agentId="a", bridgeId="b", machineId="LINUX:Laputa")
    assert claim.machineId == "linux:laputa"


def test_an_omitted_machine_id_stays_none_through_the_model():
    registered = models.AgentRegister(agentId="a", role="coder")
    assert registered.machineId is None, "the normaliser must not turn an absent field into a string"
