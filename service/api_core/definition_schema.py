"""P0 C1 admission of a definition's `agent` object, as the service receives it. PURE.

A PORT OF aify-env's `agent-definition-schema.mjs` (`idProblems`, `AGENT_RULES`, `envProblems`,
`unknownField`), rule for rule, and held to its fixture `tests/fixtures/agent-definitions/cases.json`
entry for entry (service/tests/test_definition_schema_matches_aify_env.py). A problem is the same
`<field>: <code>` string in both languages.

ONLY WHAT THE WIRE CARRIES. A snapshot entry publishes the `agent` object and its own counters; the
file's `version`, `operation` and `updatedAt`, and the rules about a file's bytes and number text, stay
with the host's store, which never sends them.
"""
from __future__ import annotations

import re
from typing import Any

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_RESERVED_DEVICE_NAMES = frozenset(["CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
                                    *(f"LPT{i}" for i in range(1, 10))])
_ENV_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}")
_MAX_ENV_VARS = 32
_MAX_ENV_VALUE_BYTES = 4096
_RESERVED_ENV_PREFIX = "AIFY_"
HARNESSES = ("claude", "codex", "hermes")
MODES = ("managed", "resident")
_MAX_NAME_CODE_POINTS = 128
_MAX_INSTRUCTIONS_BYTES = 65536
AGENT_FIELDS = ("id", "name", "role", "harness", "mode", "workspace", "model", "effort", "instructions", "env",
                "herdrSpace")
_CONTROL = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_ABSOLUTE_PATH = re.compile(r"(/|[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+)")
_NAMEABLE_KEY = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def _well_formed(text: str) -> bool:
    # A str from json.loads holds a lone surrogate as itself (a valid pair is already one code point).
    return not any(0xD800 <= ord(c) <= 0xDFFF for c in text)


def _utf8_bytes(text: str) -> int:
    return len(text.encode("utf-8"))


def id_problems(value: Any, field: str = "id") -> list[str]:
    """The problems with an agent id (or role) on its own, [] when it is admitted."""
    if not isinstance(value, str):
        return [f"{field}: type"]
    if not _well_formed(value):
        return [f"{field}: malformed-unicode"]
    if not _ID.fullmatch(value):
        return [f"{field}: pattern"]
    if value.split(".")[0].upper() in _RESERVED_DEVICE_NAMES:
        return [f"{field}: reserved-name"]
    return []


def _text_problems(value: Any, field: str, rule) -> list[str]:
    if not isinstance(value, str):
        return [f"{field}: type"]
    if not _well_formed(value):
        return [f"{field}: malformed-unicode"]
    return rule(value)


def _env_problems(env: Any) -> list[str]:
    if not isinstance(env, dict):
        return ["agent.env: type"]
    problems = ["agent.env: too-many"] if len(env) > _MAX_ENV_VARS else []
    for name, value in env.items():
        if not _ENV_NAME.fullmatch(name):
            problems.append("agent.env: bad-name")
            continue
        field = f"agent.env.{name}"
        if name.upper().startswith(_RESERVED_ENV_PREFIX):
            problems.append(f"{field}: reserved")
            continue
        problems += _text_problems(value, field, lambda text: (
            ([f"{field}: nul"] if "\x00" in text else [])
            + ([f"{field}: too-large"] if _utf8_bytes(text) > _MAX_ENV_VALUE_BYTES else [])))
    return problems


def _name_problems(text: str) -> list[str]:
    return ((["agent.name: length"] if not 1 <= len(text) <= _MAX_NAME_CODE_POINTS else [])
            + (["agent.name: control"] if _CONTROL.search(text) else []))


def _agent_id_problems(value: Any, agent_id: str) -> list[str]:
    problems = id_problems(value, "agent.id")
    return problems or (["agent.id: mismatch"] if value != agent_id else [])


_AGENT_RULES = {
    "id": _agent_id_problems,
    "name": lambda v, _: _text_problems(v, "agent.name", _name_problems),
    "role": lambda v, _: id_problems(v, "agent.role"),
    "harness": lambda v, _: [] if v in HARNESSES else ["agent.harness: unsupported"],
    "mode": lambda v, _: [] if v in MODES else ["agent.mode: unsupported"],
    "workspace": lambda v, _: _text_problems(
        v, "agent.workspace", lambda t: [] if _ABSOLUTE_PATH.match(t) else ["agent.workspace: not-absolute"]),
    "model": lambda v, _: _text_problems(v, "agent.model", lambda t: []),
    "effort": lambda v, _: _text_problems(v, "agent.effort", lambda t: []),
    "instructions": lambda v, _: _text_problems(
        v, "agent.instructions",
        lambda t: ["agent.instructions: too-large"] if _utf8_bytes(t) > _MAX_INSTRUCTIONS_BYTES else []),
    "env": lambda v, _: _env_problems(v),
    "herdrSpace": lambda v, _: [] if isinstance(v, bool) else ["agent.herdrSpace: type"],
}


def _unknown_agent_field(key: str) -> str:
    return f"agent.{key}: unknown-field" if _NAMEABLE_KEY.fullmatch(key) else "agent: unknown-field"


#: Passed for an `agent` the body does not hold at all, which C1 names apart from one of the wrong type.
MISSING = object()


def agent_problems(agent: Any, agent_id: str) -> list[str]:
    """Every C1 problem with a definition's `agent` object published for `agent_id`, sorted; [] when
    it is admitted. Nothing is defaulted: an absent field is a problem, as it is in the host's file."""
    problems = id_problems(agent_id)
    if agent is MISSING:
        return sorted(problems + ["agent: missing"])
    if not isinstance(agent, dict):
        return sorted(problems + ["agent: type"])
    problems += [_unknown_agent_field(key) for key in agent if key not in AGENT_FIELDS]
    for field in AGENT_FIELDS:
        problems += _AGENT_RULES[field](agent[field], agent_id) if field in agent else [f"agent.{field}: missing"]
    return sorted(problems)
