"""A setting the operator can change must be one something still reads.

WHY THIS EXISTS. `settings-panel.mjs` declares 35 fields; the operator sees a labelled control for
each, types a value, and it is saved. Nothing about that surface changes when the code that CONSUMED
the value is deleted or renamed -- the field still renders, still saves, and still reads back what
was typed. **A setting whose reader is gone looks exactly like one that works**, and the only way to
tell them apart is to go and look.

That is the operator's own question, asked on 2026-09-08 for the v0.6.3 pass: "do they still make
sense, are they stale, do they still work (trace from code)". This traces from code, every run.

WHAT IT CANNOT DO, said plainly rather than implied. It proves a key is NAMED somewhere outside its
own declaration. It cannot prove the reader still does what the LABEL promises -- that a field called
"Retention (days)" still bounds retention in days rather than something a refactor left it pointing
at. That judgement needs a person, and it is a separate pass. What this catches is the failure mode
that is invisible: the reader that stopped existing.

FIXTURES ARE EXCLUDED, AND WITHOUT THAT THIS GATE WOULD BE NEARLY VACUOUS.
`new_dashboard/fixtures/app.before-settings-fields.js` is a frozen PRE-EXTRACTION copy of `app.js`
and contains the entire original `SETTINGS_SCHEMA`. **MEASURED 2026-09-08: all 35 declared settings
appear inside it.** So with fixtures counted, the fixture ALONE satisfies the reader check for every
setting that exists today, and this gate could only ever catch one invented after the fixture was
frozen -- which is to say, it would pass silently on exactly the settings anyone cares about.

That measurement is here because the obvious mutation did NOT prove it: adding an invented key and
watching the gate fire proves nothing, since an invented key is not in the fixture either. The
property had to be measured directly rather than inferred from a mutant that could not fail.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PANEL = REPO / "service" / "new_dashboard" / "settings-panel.mjs"

#: Where a reader could live. The service consumes settings in Python; the dashboard consumes the
#: presentation ones in JS; the bridge reads a few when it launches a runtime.
SEARCH_ROOTS = ("service", "mcp")

#: Pruned at the DIRECTORY level, like every other scan in this repo. `fixtures` is the load-bearing
#: one -- see the module docstring.
SKIP_DIRS = {"tests", "__pycache__", "node_modules", "fixtures", ".git", ".pytest_cache"}


def declared_settings() -> list[str]:
    """Every key the operator is offered, read from the panel that offers them."""
    return re.findall(r"key: '([a-z0-9_]+)'", PANEL.read_text(encoding="utf-8"))


def _sources() -> list[tuple[Path, str]]:
    out: list[tuple[Path, str]] = []
    for base in SEARCH_ROOTS:
        for path in (REPO / base).rglob("*"):
            if not path.is_file() or path.suffix not in (".py", ".mjs", ".js"):
                continue
            if any(part in SKIP_DIRS for part in path.parts):
                continue
            if ".test." in path.name:
                continue
            out.append((path, path.read_text(encoding="utf-8", errors="ignore")))
    return out


def readers_of(key: str, sources: list[tuple[Path, str]]) -> list[str]:
    """Files naming this key, other than the panel that declares it."""
    return [
        str(path.relative_to(REPO)).replace("\\", "/")
        for path, text in sources
        if key in text and path.name != "settings-panel.mjs"
    ]


def test_the_scan_can_say_absent_and_present():
    """POSITIVE AND NEGATIVE CONTROL, in the same run, before any verdict is trusted.

    A probe that cannot return ABSENT cannot return PRESENT. If the walk broke -- a wrong root, a
    prune that ate everything, an encoding error swallowed -- every setting would report readers it
    does not have, or none would, and either way the test below would be measuring nothing.
    """
    sources = _sources()
    assert len(sources) > 200, f"only {len(sources)} source files found; the walk is broken"

    invented = "dashboard_setting_that_nothing_reads_2026"
    assert readers_of(invented, sources) == [], "the scan finds readers for a key that cannot exist"

    known = readers_of("retention_days", sources)
    assert known, "the scan finds no reader for a setting known to be consumed"


def test_the_panel_still_declares_settings():
    """The other half of the control: a parse returning nothing would make the gate vacuous."""
    keys = declared_settings()
    assert len(keys) >= 30, f"only parsed {len(keys)} settings out of the panel"
    assert "retention_days" in keys, "the parse missed a key that is plainly in the panel"


def test_every_declared_setting_is_read_by_something():
    """No setting is offered to the operator with nothing left behind it.

    ONE READER IS ENOUGH TO PASS. That is deliberate -- a setting consumed in exactly one place is
    normal, not suspicious -- and it is also the ceiling of what this can claim. It fires only on
    ZERO, which is the failure that cannot be seen from the Settings page.
    """
    sources = _sources()
    orphans = {}
    for key in declared_settings():
        found = readers_of(key, sources)
        if not found:
            orphans[key] = found
    assert orphans == {}, (
        "these settings are offered on the Settings page and nothing reads them:\n  "
        + "\n  ".join(sorted(orphans))
        + "\n\nA field whose reader is gone looks exactly like one that works: it renders, saves and "
        "reads back. Either restore the reader or remove the field -- leaving it is a control that "
        "promises an effect it no longer has."
    )
