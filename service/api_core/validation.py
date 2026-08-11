"""Name admission for the API boundary. Security-adjacent, so it gets its own home.

v0.5.1f. `validate_name` is the gate every agent id, channel name and environment name passes
through, and it lived as a casual helper in the middle of a 20,000-line router. Its rule and its
regex now sit together in one small module that can be read in full before trusting it.

Python remains the admission authority: enforcement happens at the API boundary, so this raises the
same `HTTPException` it always did. A structural move does not get to turn admission into a boolean
or an error string.

A DOCUMENTED QUIRK, PRESERVED DELIBERATELY. `re.$` matches before a trailing newline, so a name like
`"agent
"` is ACCEPTED today. That is almost certainly not intended, but changing it here would be
a behaviour change in a series whose contract is an empty behaviour changelog. It is pinned by a test
that says so out loud rather than quietly fixed or quietly ignored -- so the day someone decides to
tighten it, the test names what changes and who might break.
"""

from __future__ import annotations

import re

from fastapi import HTTPException


SAFE_NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$')


def validate_name(name: str, label: str = "name") -> None:
    if not SAFE_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail=f"Invalid {label}: must be 1-128 alphanumeric chars, dots, hyphens, underscores.")
