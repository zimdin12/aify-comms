"""Which files are "the doctor" -- derived, never listed.

Four separate scanners each hardcoded ``doctor.js`` as the answer. Moving ONE check into its own
module so a test could execute it reddened three of them at once and left the fourth quietly scanning
a file the check no longer lived in. A list you must remember to update in four places is a defect
with a delay on it, and the delay had already started: the fourth scanner was green because it had
stopped looking, not because there was nothing left to find.

So: the doctor is ``doctor.js`` plus every local module it reaches, transitively. A check that moves
into a new module is found with no edit here. The JS suite derives the same population the same way
(``mcp/stdio/tests/doctor-sources.mjs``); two implementations of one DERIVATION disagree loudly,
where two copies of one LIST agree right up until somebody edits one of them.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STDIO = ROOT / "mcp" / "stdio"

_LOCAL_IMPORT = re.compile(r'from\s+"(\.[^"]+)"')


def doctor_source_files(entry: Path | None = None) -> list[Path]:
    """Every file the doctor is built from, ``doctor.js`` first."""
    entry = entry or STDIO / "doctor.js"
    seen: list[Path] = []
    pending = [entry.resolve()]
    while pending:
        current = pending.pop(0)
        if current in seen:
            continue
        seen.append(current)
        for spec in _LOCAL_IMPORT.findall(current.read_text(encoding="utf-8")):
            pending.append((current.parent / spec).resolve())
    return seen


def doctor_source_text() -> str:
    """Those files' text, joined -- what a scanner that used to read one file should read instead."""
    return "\n".join(p.read_text(encoding="utf-8") for p in doctor_source_files())
