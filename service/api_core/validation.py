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


# `\Z`, NOT `$`. Python's `$` also matches BEFORE a trailing newline, so `"agent\n"` passed this gate
# — while `"age\nnt"` was rejected, which is what shows the intent. `test_name_validation.py` pinned
# that as a documented quirk in v0.5.1f and named the two conditions for tightening it: do it on
# purpose, and consider existing names. Both are met here.
#
# ON PURPOSE, by this suite's OWN stated principle. It rejects homoglyphs because "homoglyphs are an
# impersonation vector when a name is an identity", and this gate guards AGENT IDS at twenty-odd call
# sites. A trailing newline is the perfect homoglyph: `coder` and `coder\n` are two distinct
# identities that render identically everywhere an operator or another agent can see them.
#
# EXISTING NAMES: a name ending in a newline could only ever have been created by a client that sent
# one, since nothing in this repo generates them. If such a row exists it becomes unreachable through
# the API rather than silently renamed — a 400 the operator can see, which is the safe direction for
# an identity gate.
SAFE_NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}\Z')


def validate_name(name: str, label: str = "name") -> None:
    if not SAFE_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail=f"Invalid {label}: must be 1-128 alphanumeric chars, dots, hyphens, underscores.")


# v0.5.4: moved out of the control plane. It REFUSES a request at the API boundary, raising HTTPException,
# which is precisely this module's stated subject — admission control, security-adjacent, its own home.
def _reject_sender_truncated_body(body):
    if re.search(r"(?:\.\.\.|…)\[truncated\](?:\s*```)?\s*$", str(body or ""), re.I):
        raise HTTPException(
            422,
            "Message body was already truncated by the sender; resend a complete concise body or link a durable artifact.",
        )
