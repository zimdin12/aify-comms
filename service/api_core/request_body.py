"""The JSON object a hand-read request body must be.

Twelve handlers read `await request.json()` raw, and only two checked the result was an object. A JSON
array or string reached `body.get(...)` and raised AttributeError, and malformed JSON raised on the
handlers with no try; both came back as a 500 with a full traceback logged (v0.7 scan A3). Handlers
with a declared shape should take a Pydantic model; this is for the ones whose body is optional or
free-form.
"""

from __future__ import annotations

import json

from fastapi import HTTPException, Request


async def json_object_body(request: Request, *, lenient: bool = False) -> dict:
    """`{}` for an empty body; 400 for malformed JSON or anything that is not a JSON object.

    `lenient` is for routes shell HOOKS post to (heartbeats, turn boundaries): there a body that
    cannot be read degrades to no body rather than failing, because a beat or a turn-end that errors
    is one that never lands. It still never hands the caller a non-dict.
    """
    raw = await request.body()
    if not raw.strip():
        return {}
    try:
        body = json.loads(raw)
    except ValueError:
        if lenient:
            return {}
        raise HTTPException(400, "The request body is not valid JSON.")
    if not isinstance(body, dict):
        if lenient:
            return {}
        raise HTTPException(400, "The request body must be a JSON object.")
    return body
