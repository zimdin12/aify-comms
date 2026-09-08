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


#: Settings that are OFFERED and read by nothing, each with the open question it is waiting on.
#:
#: THIS IS NOT AN EXEMPTION LIST TO GROW. One entry, and it exists because the resolution is an
#: OPERATOR'S DECISION rather than a repair: removing a control they can see, or restoring behaviour
#: that was deliberately taken out, is not a thing to do quietly inside a test fix.
#:
#: `manual_session_mode` -- FOUND 2026-09-08, and the sharp part is that TWO TESTS CONTRADICT EACH
#: OTHER while both pass, because they check different layers:
#:
#:   settings-panel.test.mjs  "the schema still exposes manual_session_mode ... losing it silently
#:                            would remove the operator's ONLY CONTROL over those chips"
#:   session-rail.test.mjs    "IT IS NOT GATED ON A SETTING -- the chip renders whatever
#:                            manual_session_mode says ... Manual switching must stay reachable"
#:
#: So the panel promises a control the rail guarantees is inert. Either the gating comes back or the
#: field goes; both are visible to the operator and neither is mine to choose. Until then it is
#: written down HERE, where the next person auditing settings meets it, rather than being discovered
#: again by the same trace.
KNOWN_INERT = {
    "manual_session_mode": (
        "the chips are deliberately ungated (session-rail.test.mjs) while the panel calls this the "
        "operator's only control over them (settings-panel.test.mjs) -- restore the gating or remove "
        "the field; an operator decision, open since 2026-09-08"
    ),
}


def declared_settings() -> list[str]:
    """Every key the operator is offered, read from the panel that offers them."""
    return re.findall(r"key: '([a-z0-9_]+)'", PANEL.read_text(encoding="utf-8"))


#: Dict literals that DECLARE settings rather than consume them, as `(file, opening line)`.
#:
#: WITHOUT THIS THE GATE WAS NEARLY VACUOUS, and it failed in exactly the way its own docstring warns
#: about for the fixture -- one file over, undetected. `DEFAULT_SETTINGS` in `api_core/settings.py`
#: names EVERY key, so every setting had at least one "reader" and the check could only ever fire on
#: a key that appeared nowhere at all. Measured 2026-09-08: `manual_session_mode` reported exactly one
#: reader, `service/api_core/settings.py`, which is the line that declares its default.
#:
#: THE FILE IS NOT EXCLUDED, ONLY THE LITERAL. `settings.py` also holds real consumers --
#: `_managed_terminal_backing_enabled` and friends -- so dropping the whole file would swap one
#: blind spot for another. The dict is cut out and the rest of the file still counts.
DECLARATION_BLOCKS = (
    ("service/api_core/settings.py", "DEFAULT_SETTINGS"),
    # Per-key server-side FLOORS: a minimum for a value, not a use of it.
    ("service/routers/settings.py", "_SETTINGS_MIN"),
)


def _without_declarations(relative: str, text: str) -> str:
    """The file with any declaration dict literal removed, so a default is not read as a use."""
    for where, opener in DECLARATION_BLOCKS:
        if relative != where:
            continue
        start = text.find(opener)
        if start == -1:
            continue
        brace = text.find("{", start)
        if brace == -1:
            continue
        depth = 0
        for index in range(brace, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    return text[:start] + text[index + 1:]
    return text


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
            relative = str(path.relative_to(REPO)).replace("\\", "/")
            text = path.read_text(encoding="utf-8", errors="ignore")
            out.append((path, _without_declarations(relative, text)))
    return out


#: A settings key, and not a longer word that happens to start with it.
#:
#: SUBSTRING MATCHING GAVE THIS GATE A FALSE GREEN, found 2026-09-08 while tracing labels against
#: readers by hand. `manual_session_mode` reported a reader: `session_mode.py` writes an audit reason
#: string `"manual_session_mode_switch"`, which CONTAINS the key and reads nothing. Its real reader
#: had been deliberately deleted -- `session-rail.test.mjs` says so in as many words, "IT IS NOT GATED
#: ON A SETTING" -- so the operator has a toggle labelled "Show resident/managed switch chips" that
#: changes nothing, and the gate built to find exactly that was satisfied by a coincidence.
#:
#: A word boundary is the whole fix. `manual_session_mode_switch` no longer matches; every genuine
#: read -- `settings["k"]`, `settings.get("k")`, `state.settings?.k`, `DEFAULT_SETTINGS["k"]` -- still
#: does, because each ends the identifier at the key.
def _mentions(key: str, text: str) -> bool:
    return re.search(rf"\b{re.escape(key)}\b(?![A-Za-z0-9_])", text) is not None


def readers_of(key: str, sources: list[tuple[Path, str]]) -> list[str]:
    """Files naming this key AS A KEY, other than the panel that declares it."""
    return [
        str(path.relative_to(REPO)).replace("\\", "/")
        for path, text in sources
        if _mentions(key, text) and path.name != "settings-panel.mjs"
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
    # A KNOWN-INERT SETTING IS STILL REPORTED, just not as a failure -- and the entry has to
    # still be TRUE. One that gained a reader, or left the panel, fails below rather than sitting
    # here as an exemption nobody prunes.
    for key, why in KNOWN_INERT.items():
        assert key in declared_settings(), (
            f"`{key}` is recorded as a known-inert setting and is no longer offered at all; "
            "delete the entry rather than leaving it to exempt nothing"
        )
        assert key in orphans, (
            f"`{key}` is recorded as known-inert ({why}) and now HAS a reader. "
            "Delete the entry -- the open question it names has been answered."
        )
    orphans = {k: v for k, v in orphans.items() if k not in KNOWN_INERT}
    assert orphans == {}, (
        "these settings are offered on the Settings page and nothing reads them:\n  "
        + "\n  ".join(sorted(orphans))
        + "\n\nA field whose reader is gone looks exactly like one that works: it renders, saves and "
        "reads back. Either restore the reader or remove the field -- leaving it is a control that "
        "promises an effect it no longer has."
    )
