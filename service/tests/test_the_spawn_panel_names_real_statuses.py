r"""The dashboard's spawn panel names states a spawn request can actually be in.

THE DEFECT THIS WAS WRITTEN FROM. `environments-panels.mjs` did two things with a vocabulary the
service does not have:

    const chipStatus = status === 'done' ? 'completed' : status;
    ...
    <p>Queued, failed, and completed spawns will appear here.</p>

`done` is not a spawn-request status and neither is `completed`. `PATCH /spawn-requests/{id}`
validates against `SPAWN_REQUEST_PATCHABLE_STATUSES` and answers 400 for anything else; `queued` is
the column default. So the alias could never fire, its target was not a state either, and the empty
state told an operator to wait for something the system has never produced. The panel's own test
required the alias, describing what would break without it -- for a case that cannot occur.

WHY A GATE AND NOT JUST A FIX. The vocabulary is written on one side and READ ALOUD on the other,
which is a join with no compiler between it. The service now names the set once
(`SPAWN_REQUEST_STATUSES`) and this compares the panel's words against it, so the next state added or
renamed makes the operator-facing sentence fail rather than quietly go stale.

SCOPE, STATED. This governs the spawn-requests panel and the words it renders, not every status
string in the dashboard: `completed` and `done` are legitimate elsewhere -- `dispatch_runs` really is
`completed`, and `SESSION_CLEAN_HISTORY_STATUSES` names it too. A repo-wide ban on those words would
be false. What is false HERE is naming them as spawn-request states.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from service.routers.spawn_requests import (
    SPAWN_REQUEST_PATCHABLE_STATUSES,
    SPAWN_REQUEST_STATUSES,
    _SPAWN_TERMINAL_STATUSES,
)

PANEL = Path(__file__).resolve().parents[2] / "service" / "new_dashboard" / "environments-panels.mjs"

#: Words that name a lifecycle state in prose or in a comparison. Deliberately narrow: this is not a
#: search for the substring, which would match `completedAt` or an unrelated sentence.
STATUS_WORD = re.compile(r"""\b(?:status\s*===?\s*['"](?P<cmp>[a-z-]+)['"]|(?P<prose>[A-Za-z-]+) spawns)""")


def _panel_source() -> str:
    return PANEL.read_text(encoding="utf-8")


def _spawn_panel_region() -> str:
    """`renderSpawnRequests` only, comments removed.

    The file also renders environments, whose statuses differ. And the comments must go: the note
    explaining that the `done` alias was removed QUOTES the comparison it removed, and the first
    version of this gate read that sentence as a live branch and failed on the fix.
    """
    source = _panel_source()
    start = source.index("export function renderSpawnRequests()")
    end = source.index("export function", start + 10)
    return chr(10).join(
        line for line in source[start:end].splitlines()
        if not line.lstrip().startswith("//")
    )


class TheSpawnPanelNamesRealStatusesTests(unittest.TestCase):
    def test_the_vocabulary_has_one_owner_and_the_subsets_agree_with_it(self) -> None:
        """The control for the producer side. A set that named nothing would satisfy everything."""
        self.assertIn("queued", SPAWN_REQUEST_STATUSES)
        self.assertIn("running", SPAWN_REQUEST_STATUSES)
        self.assertEqual(
            SPAWN_REQUEST_PATCHABLE_STATUSES, frozenset(SPAWN_REQUEST_STATUSES) - {"queued"},
            "the patchable subset must be derived from the vocabulary, not listed beside it",
        )
        self.assertTrue(
            _SPAWN_TERMINAL_STATUSES <= set(SPAWN_REQUEST_STATUSES),
            f"a terminal status is not in the vocabulary: "
            f"{sorted(_SPAWN_TERMINAL_STATUSES - set(SPAWN_REQUEST_STATUSES))}",
        )

    def test_the_panel_region_was_found(self) -> None:
        """The other control. An empty region agrees with every assertion below."""
        region = _spawn_panel_region()
        self.assertIn("spawn-requests-list", region)
        self.assertIn("No spawn requests", region, "the empty state is not in the region being read")

    def test_the_panel_compares_against_no_status_the_service_refuses(self) -> None:
        compared = {m.group("cmp") for m in STATUS_WORD.finditer(_spawn_panel_region()) if m.group("cmp")}
        unknown = sorted(compared - set(SPAWN_REQUEST_STATUSES))
        self.assertEqual(unknown, [], (
            "the panel branches on a spawn status the service will not accept, so the branch cannot "
            f"be taken: {unknown}. The vocabulary is {list(SPAWN_REQUEST_STATUSES)}"
        ))

    def test_the_empty_state_promises_only_states_that_can_happen(self) -> None:
        """The sentence an operator reads when the table is empty, checked word by word.

        This is what made the defect visible: "Queued, failed, and completed spawns will appear
        here" invites the reader to wait for a state that has never existed.
        """
        region = _spawn_panel_region()
        promised = {m.group("prose").lower() for m in STATUS_WORD.finditer(region) if m.group("prose")}
        self.assertTrue(promised, "no '<word> spawns' phrase was found, so this test read nothing")
        unknown = sorted(promised - set(SPAWN_REQUEST_STATUSES) - {"no"})
        self.assertEqual(unknown, [], (
            "the empty state names a spawn state the service cannot produce: " + str(unknown)
        ))


if __name__ == "__main__":
    unittest.main()
