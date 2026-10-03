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
# The launch's and the launchers' namespaces (external review of 0.8.4: HARNESS_EXTRA_ENV forced a session id).
_RESERVED_ENV_PREFIXES = ("AIFY_", "HARNESS_")
# The dashboard's secret-name rule exactly. A secret name is refused under _RESERVED_ENV_PREFIXES too, one list for
# both. A child gets its env in any case on Windows, so the reserved and duplicate checks ignore case.
_SECRET_NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
_SECRETS_FIELDS = ("project", "names")
HARNESSES = ("claude", "codex", "hermes")
MODES = ("managed", "resident")
_MAX_NAME_CODE_POINTS = 128
_MAX_INSTRUCTIONS_BYTES = 65536
AGENT_FIELDS = ("id", "name", "role", "harness", "mode", "workspace", "model", "effort", "instructions", "env",
                "herdrSpace", "secrets")
# The only agent fields that may be absent. Absent `secrets` means none, and is the only way to say so: a present
# field must name a project and at least one secret.
_OPTIONAL_AGENT_FIELDS = frozenset(["secrets"])
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
        if name.upper().startswith(_RESERVED_ENV_PREFIXES):
            problems.append(f"{field}: reserved")
            continue
        problems += _text_problems(value, field, lambda text: (
            ([f"{field}: nul"] if "\x00" in text else [])
            + ([f"{field}: too-large"] if _utf8_bytes(text) > _MAX_ENV_VALUE_BYTES else [])))
    return problems


def _secret_name_problems(names: Any, env: Any) -> list[str]:
    # Only valid env names are compared: they are ASCII, so no case mapping can differ from aify-env's.
    if not isinstance(names, list):
        return ["agent.secrets.names: type"]
    if not names:
        return ["agent.secrets.names: empty"]
    problems = ["agent.secrets.names: too-many"] if len(names) > _MAX_ENV_VARS else []
    env_names = {n.upper() for n in env if _ENV_NAME.fullmatch(n)} if isinstance(env, dict) else set()
    seen: set[str] = set()
    for name in names:
        if not isinstance(name, str) or not _SECRET_NAME.fullmatch(name):
            problems.append("agent.secrets.names: bad-name")
            continue
        field = f"agent.secrets.names.{name}"
        upper = name.upper()
        if upper.startswith(_RESERVED_ENV_PREFIXES):
            problems.append(f"{field}: reserved")
            continue
        if upper in seen:
            problems.append(f"{field}: duplicate")
            continue
        seen.add(upper)
        if upper in env_names:
            problems.append(f"{field}: collides-with-env")
    return problems


def _secrets_problems(secrets: Any, env: Any) -> list[str]:
    if not isinstance(secrets, dict):
        return ["agent.secrets: type"]
    problems = [_unknown_field("agent.secrets", key) for key in secrets if key not in _SECRETS_FIELDS]
    if "project" not in secrets:
        problems.append("agent.secrets.project: missing")
    else:
        problems += _text_problems(secrets["project"], "agent.secrets.project", lambda t: (
            ["agent.secrets.project: empty"] if t == "" else [] if _ID.fullmatch(t) else ["agent.secrets.project: pattern"]))
    problems += (_secret_name_problems(secrets["names"], env) if "names" in secrets
                 else ["agent.secrets.names: missing"])
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


def _unknown_field(parent: str, key: str) -> str:
    return f"{parent}.{key}: unknown-field" if _NAMEABLE_KEY.fullmatch(key) else f"{parent}: unknown-field"


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
    problems += [_unknown_field("agent", key) for key in agent if key not in AGENT_FIELDS]
    for field in AGENT_FIELDS:
        if field == "secrets":
            problems += _secrets_problems(agent[field], agent.get("env")) if field in agent else []
        elif field in agent:
            problems += _AGENT_RULES[field](agent[field], agent_id)
        elif field not in _OPTIONAL_AGENT_FIELDS:
            problems.append(f"agent.{field}: missing")
    return sorted(problems)
