"""The extra environment a spawn asks its worker to start with.

THE DEFECT THIS CLOSES, found 2026-09-14 by review of the aify-dashboard plan and confirmed by
reading both tiers. `POST /spawn-requests` accepted `envVars` and stored them in
`spawn_specs.env_vars`, and the spawn listing returned them -- but `GET /terminals/{id}/launch`, the
one place a worker's environment is composed, never read them. aify-env merges whatever `env` that
route returns over its own base environment, so the variables were one lookup away from the process
and never took it. A caller setting one got a 200, a stored value and a worker without it.

WHAT IS ALLOWED, AND WHY THIS IS NOT A DENYLIST. A spawn is an authenticated request from somebody
who can already choose the program, the workspace and the model, so the variables are theirs to set.
The one rule that is not negotiable is enforced against what the launch WRITES rather than a list:
`managed_launch_env` lays these down first, drops any whose name matches one of its own identity and
wiring variables in any case, and puts its own on top, so a spawn can add to the worker's environment
but cannot rename the agent it is launching. Case matters because Windows treats `aify_agent_id` and
`AIFY_AGENT_ID` as one variable. A list of forbidden names would be a second copy of the names that
function writes, and would agree with it until one changed.

WHAT IS REFUSED is what cannot be an environment variable at all, or would make the overlay a way to
ship a whole environment over the wire -- which `GET /terminals/{id}/launch` exists to never do.

AND THE `AIFY_` NAMESPACE, in any case. The launch writes twelve of those names on every worker, and
the bridge reads dozens more that it does not write -- `AIFY_SERVER_URL`, `AIFY_API_KEY`,
`AIFY_AGENT_RUNTIME` -- so dropping only the written ones let a spawn point its worker at another
service or change the runtime it reports, while the launch's own identity looked intact. The whole
prefix is the launch's: a prefix is the namespace rule itself, where a list of reserved names would be
a third copy of what the bridge reads.

PURE: no database, no environment read.
"""
from __future__ import annotations

import re
from typing import Any

#: A POSIX-portable variable name. Windows accepts more, but a name only one platform can hold is a
#: spawn that works on one host and silently loses its variable on the other.
SPAWN_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}\Z")

#: Enough for real configuration, small enough that the launch overlay stays an overlay.
MAX_SPAWN_ENV_VARS = 32

#: Per value, in UTF-8 bytes. Windows' whole environment block is capped at 32,767 characters.
MAX_SPAWN_ENV_VALUE_BYTES = 4096

#: Names the launch and the aify bridge own. Compared upper-cased: Windows reads `aify_server_url`
#: and `AIFY_SERVER_URL` as one variable.
RESERVED_SPAWN_ENV_PREFIX = "AIFY_"


def spawn_env_problems(env_vars: Any) -> list[str]:
    """Every reason this `envVars` cannot be stored, or [] when it can. Absent is valid."""
    if env_vars is None:
        return []
    if not isinstance(env_vars, dict):
        return ["envVars must be an object of NAME: string value"]
    problems: list[str] = []
    if len(env_vars) > MAX_SPAWN_ENV_VARS:
        problems.append(f"envVars has {len(env_vars)} entries; the limit is {MAX_SPAWN_ENV_VARS}")
    for name, value in env_vars.items():
        if not isinstance(name, str) or not SPAWN_ENV_NAME.match(name):
            problems.append(f"envVars name {name!r} is not a valid variable name ([A-Za-z_][A-Za-z0-9_]*)")
        elif name.upper().startswith(RESERVED_SPAWN_ENV_PREFIX):
            problems.append(f"envVars {name}: {RESERVED_SPAWN_ENV_PREFIX}* names are set by the launch, not by a spawn")
        elif not isinstance(value, str):
            # NOT COERCED. `True` would become "True" and `None` would become "None" -- a value the
            # caller never wrote, which the worker would then read as though they had.
            problems.append(f"envVars {name} must be a string, not {type(value).__name__}")
        elif "\x00" in value:
            problems.append(f"envVars {name} contains a NUL byte, which no environment can hold")
        elif len(value.encode("utf-8")) > MAX_SPAWN_ENV_VALUE_BYTES:
            problems.append(f"envVars {name} is over {MAX_SPAWN_ENV_VALUE_BYTES} bytes")
    return problems


def spawn_env_overlay(env_vars: Any) -> dict[str, str]:
    """The stored `envVars` as launch variables.

    ONLY WHEN THE WHOLE VALUE IS VALID. Rows written before `POST /spawn-requests` validated could
    hold anything; launching with the valid half would start a worker with a configuration nobody
    asked for, and launching with none says exactly what the service can vouch for.
    """
    if not env_vars or spawn_env_problems(env_vars):
        return {}
    return dict(env_vars)
