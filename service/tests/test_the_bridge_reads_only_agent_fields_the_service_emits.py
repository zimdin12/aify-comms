r"""Every `info.X` the bridge's deliverability classifier reads is a field the agent payload emits.

THE SYMMETRIC HALF THAT DID NOT EXIST. `test_the_dashboard_reads_only_agent_fields_the_service_emits`
watches one consumer of the agent payload. The BRIDGE is the other, and it is the one that decides
whether work reaches an agent -- `wakeModeSummary` in `mcp/stdio/agent-summary.mjs` is a fourteen-branch
classifier whose answers are what `comms_agents` and `comms_agent_info` show an operator, and what
tells them whether a silent agent is idle or structurally unreachable. Nothing compared its reads
against what the service sends.

WHAT THAT COST, found by writing this. Both functions read `info.machineId || info.machine_id` and
`info.sessionMode || info.session_mode`. Neither alternate can arrive. Measured three ways on
2026-08-29:

  * the live `/agents` and `/agents/{id}` carry ZERO snake_case keys on an agent record;
  * the service's own assembly -- `_agent_record_to_dict` plus the two `_enforce_*` gates -- emits 32
    fields and not one is snake_case;
  * the LOCAL registry, the other producer that feeds these functions, writes `machineId` and
    `sessionMode` (`registration-tool.mjs`), and nothing in the bridge writes the other pair.

An existing test asserted the opposite in as many words -- "snake_case arrives from the service
alongside camelCase; both must be read" -- so this is a claim about the wire that was written down,
believed, and false. It now pins the absence.

WHY A DEAD ALTERNATE IS WORSE HERE THAN ON THE DASHBOARD. `normalizeSessionMode` fails toward
`resident`. If `sessionMode` were ever renamed, `|| info.session_mode` would read like coverage for
exactly that rename while every MANAGED agent was silently classified resident, and the wake-mode
summary is the thing an operator reads to decide whether to intervene.

SCOPE, STATED RATHER THAN IMPLIED. This governs `agent-summary.mjs`, whose two functions take an
agent record and are the ones classifying deliverability. It does NOT govern every `info.X` in the
bridge: `info` is a general name there and several modules use it for spawn requests, runs and
threads, so a bridge-wide scan would compare unrelated objects against the agent payload. It also
speaks for the REMOTE path: the local registry writes a smaller key set (no `capabilities`,
`runtimeConfig` or `wakeMode`), which is why the classifier's capability branches fall through in
local mode -- correct for a registry with no service behind it, and not something this asserts.
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path

from service.tests.test_the_dashboard_reads_only_agent_fields_the_service_emits import emitted_fields

REPO = Path(__file__).resolve().parents[2]
SUMMARY = REPO / "mcp" / "stdio" / "agent-summary.mjs"

#: `info.X`, off comment lines. The bridge names the record `info` in these two functions.
INFO_READ = re.compile(r"\binfo\.([a-zA-Z_]\w*)\b")


def summary_reads() -> dict[str, list[str]]:
    """field -> where it is read, for `agent-summary.mjs`, excluding comment lines."""
    hits: dict[str, list[str]] = {}
    for number, line in enumerate(SUMMARY.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith("//") or stripped.startswith("*"):
            continue
        for match in INFO_READ.finditer(line):
            hits.setdefault(match.group(1), []).append(f"agent-summary.mjs:{number}")
    return hits


class TheBridgeReadsOnlyAgentFieldsTheServiceEmits(unittest.TestCase):
    def setUp(self) -> None:
        self.emitted, self.assembly = emitted_fields()
        self.reads = summary_reads()

    def test_BOTH_SCANS_FOUND_THEIR_SUBJECT(self):
        """The control, and the reason it comes first. Two empty sets agree perfectly and the
        comparison below would pass having read nothing -- the wrong zero this repo keeps paying for."""
        self.assertEqual(len(self.assembly), 3, f"the service-side scan found {self.assembly}")
        self.assertGreater(len(self.emitted), 25, f"only {len(self.emitted)} emitted fields found")
        self.assertGreater(len(self.reads), 5, f"only {len(self.reads)} info.X reads found")

    def test_NO_BRIDGE_READ_IS_A_FIELD_THE_SERVICE_NEVER_SENDS(self):
        missing = sorted(
            f"{field} ({', '.join(where)})"
            for field, where in self.reads.items() if field not in self.emitted
        )
        self.assertEqual(missing, [], (
            "the deliverability classifier reads fields the agent payload does not carry:\n  "
            + "\n  ".join(missing)
            + "\nJavaScript hands back undefined, the branch takes its falsy path, and nothing is "
              "logged. Either the service stopped sending it -- in which case the classifier is "
              "already wrong -- or it never did, and the read is a dead alternate that reads like "
              "coverage for the rename it cannot catch."
        ))

    def test_A_KNOWN_FIELD_IS_ON_BOTH_SIDES(self):
        """Proves the two sets are talking about the same object. Without it, a scan that read the
        wrong file and a payload that emitted nothing would agree beautifully."""
        for field in ("sessionMode", "runtime", "capabilities"):
            self.assertIn(field, self.emitted, f"{field} is not in the emitted set")
            self.assertIn(field, self.reads, f"{field} is not read by the classifier")

    def test_THE_SCAN_CAN_SAY_NO(self):
        """NEGATIVE CONTROL, on text written to fail: the exact alternates removed on 2026-08-29."""
        planted = "  const machine = info.machineId || info.machine_id || MACHINE_ID;"
        found = {m.group(1) for m in INFO_READ.finditer(planted)}
        self.assertEqual(sorted(found), ["machineId", "machine_id"])
        self.assertNotIn("machine_id", self.emitted, "the payload has grown a snake_case key")

    def test_the_scan_ignores_a_comment(self):
        """The removal left a comment block naming both alternates. A scanner that read comments
        would report them as live reads forever, and the only way to green it would be deleting the
        explanation."""
        self.assertNotIn("machine_id", self.reads, (
            "machine_id is being counted as a read; it appears only in the comment that records why "
            "it was removed"
        ))
        self.assertNotIn("session_mode", self.reads)

    def test_the_payload_carries_no_snake_case_at_all(self):
        """The general form of the finding. Every field on an agent record is camelCase, so ANY
        snake_case read in a consumer is dead by construction."""
        snake = sorted(field for field in self.emitted if "_" in field)
        self.assertEqual(snake, [], f"the agent payload now emits snake_case fields: {snake}")


if __name__ == "__main__":
    unittest.main()
