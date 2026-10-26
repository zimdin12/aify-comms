"""Credentials out of diagnostic text that is stored and served.

A worker's last terminal line becomes the stated cause when it dies, and that cause is written to the
spawn request, the run and the terminal record, then served by the API and shown on the dashboard. On
2026-10-01 that line was a hermes launcher's "gateway host ready: {..."token":"..."}", so a live
gateway token sat in a spawn request's error for two weeks (2026-10-14 audit, L3).

This rewrites the VALUE of anything named like a credential and keeps the name, so the line still says
what happened. It is applied where a failure line is chosen (before it is stored) and where error
fields are serialized (for rows stored before this existed). Screen content in a live console is not
touched: the operator watching a terminal sees what the terminal shows.
"""

from __future__ import annotations

import re

REDACTED = "[redacted]"

# A name that holds a credential, then `:` or `=`, then its value (quoted or bare). The name part
# allows prefixes such as `access_token`, `x-api-key` or `gatewayToken`. A `Bearer` scheme word, or a
# value already redacted, is not the value.
_NAMED = re.compile(
    r"""(?ix)
    ( ["']? [\w-]*? (?:token|api[_-]?key|secret|password|passwd|authorization) ["']? \s* [:=] \s* ["']? )
    (?!bearer\b|\[redacted\]) ( [^\s"'&,;}]{6,} )
    """
)
_BEARER = re.compile(r"(?i)(\bbearer\s+)([A-Za-z0-9._~+/=-]{6,})")


def redact_secrets(text: str) -> str:
    """`text` with every credential value replaced by `[redacted]`; anything else unchanged."""
    if not text:
        return text or ""
    text = _BEARER.sub(lambda m: m.group(1) + REDACTED, text)
    return _NAMED.sub(lambda m: m.group(1) + REDACTED, text)


def redact_config(value):
    """A stored runtime config or state, as an agent record serves it: `redact_secrets` on every string.

    A hermes agent's `runtimeConfig.gatewayUrl` carries its gateway's session token, and the agent
    listing served it to every caller holding the shared API key, so any agent could drive another's
    session, and a record dumped into a transcript carried a live token (2026-10-26). Every string is
    walked rather than that one field named, so the next credential stored in a config is covered too.
    The stored row keeps the token: the operator's "Open Hermes UI" reads it from
    `GET /agents/{id}/hermes-gateway`, and the service's own delivery checks read the row.
    """
    if isinstance(value, str):
        return redact_secrets(value)
    if isinstance(value, dict):
        return {key: redact_config(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_config(item) for item in value]
    return value
