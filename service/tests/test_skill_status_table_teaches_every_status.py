"""The skills must teach every status the product can actually report.

THIS EXACT FAILURE ALREADY REACHED USERS, and `service/contracts/vocabulary.json` records it in its
own header: "the agent status vocabulary in the debug skill still taught SIX states for months after
`starting` and `misconfigured` shipped, which meant an agent could read the skill and RESTART a
worker that was already booting." That is the worst possible shape for a documentation gap — the doc
did not merely omit a state, it taught an action that destroys the thing the missing state describes.

The contract file, the Python loader, the JS copy and the status engine are all held to each other by
`test_vocabulary_contract.py` and `test_status_vocabulary_binding.py`. The SKILLS are not. One of
those tests even names the skill table in a failure message — "and so must the skill status table" —
but nothing reads it, so the reminder relies on whoever is looking at the failure.

Both tables are correct today. That is the point: they are correct by attention, and the next status
to ship has nothing stopping it from repeating 2026-06-18. Checked in BOTH directions — a status the
product can report and the skill does not teach is the original incident; a status the skill teaches
that the product cannot report sends an agent hunting a state that will never appear.

All four copies are checked directly rather than relying on the mirror gate, because a mirror test
proves the two are identical, not that either is right.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from service.api_core.vocabulary import AGENT_STATUSES

REPO = pathlib.Path(__file__).resolve().parents[2]

# Every file that presents the status vocabulary as a table an agent is meant to act on.
STATUS_DOCS = [
    ".claude/skills/aify-comms/references/operations.md",
    ".claude/skills/aify-comms-debug/references/status-model.md",
    ".agents/skills/aify-comms/references/operations.md",
    ".agents/skills/aify-comms-debug/references/status-model.md",
]

# The two documents head the column differently — `Status` in the operations reference, `Label` in
# the debug one. Both are accepted rather than normalised, because renaming a heading in somebody
# else's document to satisfy a test is the test dictating prose.
_HEADER = re.compile(r"^\|\s*(?:Status|Label)\s*\|", re.IGNORECASE)
_ROW = re.compile(r"^\|\s*`([a-z_]+)`\s*\|")


def _status_table(path: pathlib.Path) -> list[str]:
    """The first column of the table whose header first cell is `Status` or `Label`.

    Scoped to that ONE table rather than every backticked cell in the file: these documents contain
    other tables, and a looser scan would quietly stop being an assertion about the status table at
    all.
    """
    rows: list[str] = []
    in_table = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if _HEADER.match(line):
            in_table = True
            continue
        if not in_table:
            continue
        if line.startswith("|---") or line.startswith("| ---"):
            continue
        match = _ROW.match(line)
        if match:
            rows.append(match.group(1))
            continue
        if not line.startswith("|"):
            break  # the table ended
    return rows


@pytest.mark.parametrize("relative", STATUS_DOCS)
def test_the_status_table_teaches_exactly_the_canonical_statuses(relative):
    path = REPO / relative
    assert path.is_file(), f"{relative} is missing — the skills no longer teach the status model"

    taught = _status_table(path)
    assert taught, f"no `| Status |` table found in {relative} — the table moved or changed shape"

    missing = [status for status in AGENT_STATUSES if status not in taught]
    extra = [status for status in taught if status not in AGENT_STATUSES]

    assert not missing, (
        f"{relative} does not teach {missing}. This is the 2026-06-18 incident: the skill taught six "
        f"states after eight shipped, so an agent read it and restarted a worker that was already "
        f"booting. Add the row — meanings live in service/contracts/vocabulary.json."
    )
    assert not extra, (
        f"{relative} teaches {extra}, which the product cannot report. An agent will wait for a state "
        f"that never arrives."
    )


# NOT ASSERTED: table ORDER. I wrote an order check first and it was a rule I invented rather than
# one the product has. The operations reference happens to match the contract's order; the debug
# reference lists `starting` and `misconfigured` LAST because they were added later, which is
# honest about their history and costs a reader nothing. Forcing a document to reshuffle so a test
# can compare lists would be the test dictating prose.


def test_the_extractor_finds_the_status_table_and_stops_at_its_end():
    """Anti-vacuity: it must find the eight rows, and it must not run past the table.

    My first version tried to prove the scoping against a real document that turned out to contain
    only ONE such table, so it proved nothing. A synthetic file exercises it directly instead.
    """
    for relative in STATUS_DOCS:
        assert len(_status_table(REPO / relative)) == 8, relative

    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        sample = pathlib.Path(tmp) / "sample.md"
        sample.write_text(
            "| Status | Meaning |\n"
            "|---|---|\n"
            "| `working` | live |\n"
            "| `online` | idle |\n"
            "\n"
            "Some prose, then an unrelated table that must NOT be swept up:\n"
            "\n"
            "| Tool | Use |\n"
            "|---|---|\n"
            "| `comms_send` | send |\n",
            encoding="utf-8",
        )
        assert _status_table(sample) == ["working", "online"], (
            "the extractor ran past the status table into the next one"
        )


def test_the_two_status_documents_are_not_the_same_file():
    """They teach the model at different depths — the debug reference explains each state, the
    operations reference gives the action. If they ever became copies, one of them is redundant and
    this file is checking one document twice."""
    operations = (REPO / STATUS_DOCS[0]).read_text(encoding="utf-8")
    status_model = (REPO / STATUS_DOCS[1]).read_text(encoding="utf-8")
    assert operations != status_model
