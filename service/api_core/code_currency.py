"""Is the process serving an environment running the code that is on its disk?

B4, and the operator's own words about the alternative: *"i never go to that path... some random path
for aify-comms doctor... no. will never use it."* The comparison existed and only a human could make
it -- one command printed what a daemon LOADED, another printed what is on DISK, and somebody had to
run both and compare eight hex characters by eye.

`bridge-current` USED TO ANSWER A VERSION OF THIS AND ITS SUBJECT MOVED OUT FROM UNDER IT. It compared
each live bridge's self-reported `bridgeBuild` against this repo's HEAD. v0.6.1 retired the tier that
reported one: the heartbeat now comes from aify-env, which sends `bridgeVersion` and `bridgeStartedAt`
and no `bridgeBuild`, because it is not built from this checkout and has no sha of this repo to be
current with. The check is not broken -- it correctly reports no evidence -- but the useful question
survived the tier that used to answer it, and ten aify-env commits sitting inert until a restart is
exactly the signal that matters.

SO THIS IS A DIFFERENT SUBJECT WITH A DIFFERENT SHAPE, and it is deliberately NOT folded into
`bridge-current`'s verdict. One verdict over two subjects cannot say which of them is wrong, which is
the gate-granularity defect this project hit six times in two days.

WHAT IT COMPARES. An advertiser reports two identities computed the same way: `instance`, the build it
LOADED, and `codeOnDisk`, the build that is there now. Equal means current, different means a restart
would pick something up. NEITHER NUMBER MEANS ANYTHING ON ITS OWN, so both travel and the verdict is
never the only thing said -- the remedy is a restart, restarting an environment tier reaps the managed
workers it is running, and advice with that price on it has to be arguable.

DERIVED ONCE, HERE, so nobody derives it again. The doctor and `comms_envs` each grew their own copy
of the claim rule and each got it wrong once; the dashboard reads a field rather than reimplementing a
comparison in a browser bundle.

NO SERVICE-SPECIFIC KNOWLEDGE ANYWHERE NEAR IT. "Which of its own files is it running" is a fact any
environment tier can report about itself, so this reads the advertisement and never names aify-env.
"""

from __future__ import annotations

from typing import Any

#: The three answers, and there is deliberately no boolean. A two-valued field is how the third state
#: gets collapsed back into one of the other two by the next person writing a summary line -- and the
#: third state here is the common one during an upgrade, which is exactly when this is being read.
CURRENT = "current"
STALE = "stale"
UNKNOWN = "unknown"


def code_currency(metadata: Any) -> dict[str, str]:
    """The currency verdict for one environment row, from what it advertised about itself.

    UNKNOWN WHENEVER EITHER HALF IS MISSING, and this is the case that must not read as a pass. An
    advertiser too old to send `codeOnDisk` sends nothing, not an empty string -- an empty value
    compared against a real build would report every such host stale and send its operator to restart
    a daemon that was fine. Both absences arrive here as the same missing key and both mean the same
    thing: nothing here has been verified.

    This project has shipped "no evidence" as a pass twice -- `env-bridge` counting registered rows
    while zero bridges were alive, and `bridge-current` green-by-default when nothing reported. Both
    were checks that could not gather evidence and said so as ok.
    """
    if not isinstance(metadata, dict):
        return {"state": UNKNOWN, "running": "", "onDisk": ""}
    running = str(metadata.get("instance") or "").strip()
    on_disk = str(metadata.get("codeOnDisk") or "").strip()
    if not running or not on_disk:
        # Both halves still travel when they can, because a reader showing "unknown" is more useful
        # when it can also show which of the two it did get.
        return {"state": UNKNOWN, "running": running, "onDisk": on_disk}
    state = CURRENT if running == on_disk else STALE
    return {"state": state, "running": running, "onDisk": on_disk}
