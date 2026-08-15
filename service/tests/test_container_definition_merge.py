"""How a container definition merges with the defaults, and which `shared_with` graphs are refused.

`load_container_definitions` turns the `containers` block of service.json into the definitions the
manager starts. Nothing named it. Its merge is ONE LEVEL DEEP by design — a nested dict in a
definition is merged key-by-key with the default of the same name, while everything else replaces
outright — and that asymmetry is the whole reason to test it: if it silently deepened or flattened,
a definition would inherit settings it never asked for, or lose ones it relied on, and the container
would start with the wrong resources rather than fail.

`shared_with` is the other half: a container that borrows another's URL instead of starting its own.
An unresolvable reference RAISES at load, which is the right moment — the alternative is a container
that starts and proxies to nothing.
"""
from __future__ import annotations

import pytest

from service.containers.manager import load_container_definitions


def config(defaults=None, **definitions):
    return {"containers": {"defaults": defaults or {}, "definitions": definitions}}


# ── the merge ────────────────────────────────────────────────────────────────────────────────
def test_a_definition_with_no_defaults_stands_alone():
    definitions, defaults = load_container_definitions(config(app={"image": "nginx:1"}))
    assert set(definitions) == {"app"}
    assert definitions["app"].image == "nginx:1"
    assert defaults == {}


def test_defaults_fill_in_what_a_definition_omits():
    definitions, defaults = load_container_definitions(
        config({"image": "base:1", "idle_timeout_seconds": 900}, app={"auto_start": True})
    )
    assert definitions["app"].image == "base:1"
    assert definitions["app"].idle_timeout_seconds == 900
    assert definitions["app"].auto_start is True
    assert defaults == {"image": "base:1", "idle_timeout_seconds": 900}, "the defaults are returned as read"


def test_a_definition_overrides_a_scalar_default():
    definitions, _ = load_container_definitions(
        config({"image": "base:1", "internal_port": 8080}, app={"image": "app:2", "internal_port": 9000})
    )
    assert definitions["app"].image == "app:2"
    assert definitions["app"].internal_port == 9000


def test_a_nested_dict_is_MERGED_key_by_key_not_replaced():
    """THE ASYMMETRY. `environment` in a definition adds to the default environment rather than
    replacing it — a container that sets one variable keeps the shared ones."""
    definitions, _ = load_container_definitions(config(
        {"image": "base:1", "environment": {"SHARED": "yes", "LEVEL": "info"}},
        app={"environment": {"LEVEL": "debug", "OWN": "1"}},
    ))
    assert definitions["app"].environment == {"SHARED": "yes", "LEVEL": "debug", "OWN": "1"}


def test_a_list_default_is_REPLACED_not_concatenated():
    """Lists are not dicts, so they take the replace path. A command must be exactly what was asked
    for — appending to an inherited one would run something nobody wrote."""
    definitions, _ = load_container_definitions(config(
        {"image": "base:1", "command": ["serve", "--port", "8080"]},
        app={"command": ["worker"]},
    ))
    assert definitions["app"].command == ["worker"]


def test_the_merge_is_only_one_level_deep():
    """A dict INSIDE a merged dict replaces wholesale — the merge does not recurse. Pinned because
    the shallow behaviour is invisible at the call site and a deepening "fix" would change what
    starts."""
    definitions, _ = load_container_definitions(config(
        {"image": "base:1", "labels": {"team": "core", "tier": "1"}},
        app={"labels": {"tier": "2"}},
    ))
    assert definitions["app"].labels == {"team": "core", "tier": "2"}, "one level DOES merge"


def test_a_nested_dict_with_no_matching_default_is_taken_whole():
    definitions, _ = load_container_definitions(
        config({"image": "base:1"}, app={"environment": {"OWN": "1"}})
    )
    assert definitions["app"].environment == {"OWN": "1"}


def test_a_dict_replacing_a_non_dict_default_takes_the_replace_path():
    """Both sides must be dicts to merge; otherwise the definition simply wins. Without the
    isinstance check on the DEFAULT this would raise instead."""
    definitions, _ = load_container_definitions(
        config({"image": "base:1", "labels": ""}, app={"labels": {"tier": "2"}})
    )
    assert definitions["app"].labels == {"tier": "2"}


def test_definitions_do_not_leak_into_each_other():
    """Each starts from a fresh copy of the defaults. A shared dict would have the second container
    inheriting the first's overrides — the classic aliasing bug in a merge like this."""
    definitions, _ = load_container_definitions(config(
        {"image": "base:1", "environment": {"SHARED": "yes"}},
        first={"environment": {"ONLY_FIRST": "1"}},
        second={"environment": {"ONLY_SECOND": "1"}},
    ))
    assert definitions["first"].environment == {"SHARED": "yes", "ONLY_FIRST": "1"}
    assert definitions["second"].environment == {"SHARED": "yes", "ONLY_SECOND": "1"}


def test_the_returned_defaults_are_not_polluted_by_the_merge():
    defaults_in = {"image": "base:1", "environment": {"SHARED": "yes"}}
    _, defaults_out = load_container_definitions(
        {"containers": {"defaults": defaults_in, "definitions": {"app": {"environment": {"OWN": "1"}}}}}
    )
    assert defaults_out["environment"] == {"SHARED": "yes"}, "the default dict must survive unedited"


def test_unknown_keys_are_ignored_rather_than_fatal():
    """`extra="ignore"` on the model — service.json carries `_comment` fields, and a config with a
    note in it must not fail the whole service at boot."""
    definitions, _ = load_container_definitions(
        config(app={"image": "nginx:1", "_comment": "why this exists", "future_option": 7})
    )
    assert definitions["app"].image == "nginx:1"


# ── shared_with ──────────────────────────────────────────────────────────────────────────────
def test_a_resolvable_shared_with_is_accepted():
    definitions, _ = load_container_definitions(config(
        primary={"image": "llm:1"},
        secondary={"image": "llm:1", "shared_with": "primary"},
    ))
    assert definitions["secondary"].shared_with == "primary"


def test_an_unresolvable_shared_with_raises_at_load():
    """Better here than at start: a container sharing a URL that does not exist proxies to nothing,
    and that failure would surface as an unexplained timeout much later."""
    with pytest.raises(ValueError) as excinfo:
        load_container_definitions(config(
            secondary={"image": "llm:1", "shared_with": "missing"},
        ))
    message = str(excinfo.value)
    assert "secondary" in message and "missing" in message
    assert "Available" in message, "the error names what COULD have been referenced"


def test_a_forward_reference_is_fine_because_validation_is_a_second_pass():
    """Definition order in the file must not matter — the check runs after all are built."""
    definitions, _ = load_container_definitions(config(
        secondary={"image": "llm:1", "shared_with": "primary"},
        primary={"image": "llm:1"},
    ))
    assert set(definitions) == {"primary", "secondary"}


def test_a_shared_with_inherited_from_defaults_is_still_validated():
    """The check reads the MERGED definition, so a bad reference cannot hide in the defaults block."""
    with pytest.raises(ValueError):
        load_container_definitions(config({"image": "base:1", "shared_with": "missing"}, app={}))


def test_an_empty_shared_with_is_not_a_reference():
    definitions, _ = load_container_definitions(config(app={"image": "nginx:1", "shared_with": ""}))
    assert definitions["app"].shared_with == ""


# ── the degenerate configs ───────────────────────────────────────────────────────────────────
def test_a_config_with_no_containers_block_is_empty_not_an_error():
    """A service.json that manages no containers is the normal case for most installs."""
    for data in ({}, {"containers": {}}, {"containers": {"definitions": {}}}):
        definitions, defaults = load_container_definitions(data)
        assert definitions == {}
        assert defaults == {}


def test_a_definition_missing_a_required_field_fails_loudly():
    """`image` has no default. A definition without one cannot start anything, so it must not load."""
    with pytest.raises(Exception):
        load_container_definitions(config(app={"internal_port": 9000}))
