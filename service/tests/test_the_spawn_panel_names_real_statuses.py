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

import inspect
import json
import re
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path

from service.api_core.agent_lifecycle_requests import claim_lifecycle_requests
from service.lifecycle_models import OPEN_STATUSES, TERMINAL
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
    """The legacy queue renderer only, comments removed.

    The file also renders environments, whose statuses differ. And the comments must go: the note
    explaining that the `done` alias was removed QUOTES the comparison it removed, and the first
    version of this gate read that sentence as a live branch and failed on the fix.
    """
    source = _panel_source()
    start = source.index("export function renderLegacySpawnRequests(")
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
        self.assertIn("legacy-spawn-requests-list", region)
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


class _ChipAttributes(HTMLParser):
    def __init__(self, markup):
        super().__init__()
        self.chips = []
        self.feed(markup)

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == 'span' and 'data-status-kind' in fields:
            self.chips.append(fields)


class LifecycleSpawnPanelTests(unittest.TestCase):
    def test_the_table_renders_the_service_lifecycle_statuses(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('node is not on PATH')
        # The schema's pending default and claimable states are in OPEN_STATUSES. Result reports
        # accept TERMINAL; the claim writer can also assign a state directly before any report.
        direct_writes = set(re.findall(r"UPDATE\s+agent_lifecycle_requests\s+SET\s+status\s*=\s*'(\w+)'",
                                       inspect.getsource(claim_lifecycle_requests), re.IGNORECASE))
        self.assertIn('claimed', direct_writes, 'control: the direct writer scan found no claim')
        statuses = sorted(set(OPEN_STATUSES) | set(TERMINAL) | direct_writes)
        unknown = 'online'  # Recognised for agents, never a lifecycle request state.
        self.assertNotIn(unknown, statuses)
        outcome = 'Launcher said "<fixture>"'
        source = f'''
            import {{ renderSpawnRequests }} from {json.dumps(PANEL.as_uri())};
            import {{ state }} from {json.dumps(PANEL.with_name('state.mjs').as_uri())};
            const table = {{ innerHTML: '' }};
            const markup = {{}};
            globalThis.document = {{ getElementById: id => id === 'spawn-requests-list' ? table : null }};
            state.spawnRequestsStatus = 'ready';
            for (const status of {json.dumps([*statuses, unknown])}) {{
              state.spawnRequests = [{{ id: 'fixture', agentId: 'coder', action: 'spawn', status,
                outcome: {json.dumps(outcome)} }}];
              renderSpawnRequests();
              markup[status] = table.innerHTML;
            }}
            console.log(JSON.stringify(markup));
        '''
        rendered = subprocess.run([node, '--input-type=module', '-e', source], check=True,
                                  capture_output=True, text=True, encoding='utf-8')
        rows = json.loads(rendered.stdout)
        for status in statuses:
            with self.subTest(status=status):
                [chip] = _ChipAttributes(rows[status]).chips
                tone = 'warn' if status in OPEN_STATUSES else 'ok' if status == 'done' else 'bad'
                dot = 'working' if tone == 'warn' else tone
                self.assertEqual((chip['data-status-kind'], chip['data-tone']), (status, tone))
                self.assertEqual(chip['data-status-why'], outcome)
                self.assertIn(f'class="status-dot {dot}"', rows[status])
                self.assertNotIn('status-dot unknown', rows[status])
        [chip] = _ChipAttributes(rows[unknown]).chips
        self.assertEqual((chip['data-status-kind'], chip['data-tone']), ('unknown', 'muted'))


if __name__ == "__main__":
    unittest.main()
