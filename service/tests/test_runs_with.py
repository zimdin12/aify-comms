"""Agent info says what model and effort an agent's next start uses, and where each comes from (P0 C12).

`runs_with` is pure; the routes that carry it (`GET /agents`, `GET /agents/{id}`) are covered for managed
agents in test_api_v2_regressions.py and for a defined agent below, through a real definition push.
"""

from __future__ import annotations

import json

from service.api_core.definition_records import runs_with


class Row(dict):
    def keys(self):  # sqlite3.Row's shape: indexable by name, with keys()
        return super().keys()


def row(**over):
    return Row({"definition_state": "", "session_mode": "managed", "model": "", "runtime_config": "{}", **over})


def definition(**agent):
    return Row({"body": json.dumps({"model": "", "effort": "", **agent})})


def test_a_defined_agent_runs_with_its_definition_even_when_its_record_says_otherwise():
    shown = runs_with(row(definition_state="defined", model="stale", runtime_config='{"effort": "low"}'),
                      definition(model="opus", effort="high"))
    assert shown == {"model": {"value": "opus", "from": "definition"}, "effort": {"value": "high", "from": "definition"}}


def test_an_empty_value_is_the_runtimes_own_never_shown_as_a_value():
    shown = runs_with(row(definition_state="defined"), definition(model="opus"))
    assert shown["effort"] == {"value": "", "from": "runtime"}


def test_an_undefined_managed_agent_runs_with_its_record_and_thinking_is_an_effort():
    assert runs_with(row(model="gpt-5.5", runtime_config='{"thinking": "xhigh"}'), None) == {
        "model": {"value": "gpt-5.5", "from": "agent"}, "effort": {"value": "xhigh", "from": "agent"}}


def test_an_undefined_resident_runs_with_the_runtimes_own_whatever_its_record_holds():
    # Its launcher reads no record (only a definition), so a model there is not what it runs with.
    shown = runs_with(row(session_mode="resident", model="opus", runtime_config='{"effort": "high"}'), None)
    assert shown == {"model": {"value": "", "from": "runtime"}, "effort": {"value": "", "from": "runtime"}}


def test_a_withdrawn_definition_is_not_what_it_runs_with():
    shown = runs_with(row(definition_state="withdrawn", model="m"), definition(model="old"))
    assert shown["model"] == {"value": "m", "from": "agent"}
