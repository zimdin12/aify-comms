"""No document may teach `aify-comms <argument>`, because that command was REMOVED and it used to
take the fleet down.

THE DEFECT, found 2026-09-05 by reading the docs rather than the code. Five operator-facing pages
still instructed the reader to run things like `aify-comms http://192.0.2.10:8800` and
`aify-comms /mnt/c/Docker /home/you/work` -- `docs/HERMES_INTEGRATION.md` under a heading called
"Start The Environment Bridge", `docs/BRIDGE_SETUP.md`, and both mirrors of the debug skill's
dispatch-launch reference.

WHY THAT IS WORSE THAN A STALE SENTENCE. Until v0.6.1 those exact words started an ENVIRONMENT
BRIDGE, which by design superseded the bridge already serving the host -- so the older one exited and
its managed workers were reaped. It happened on 2026-08-11 from a four-second run meant only to check
that the launcher still worked, and again on 2026-08-20 from a backtick inside an unquoted heredoc.
On a current install the line merely exits 2; on any host still running an older launcher it is the
command that kills the agents. And these are troubleshooting pages, so they are read at exactly the
moment somebody is willing to try anything.

THE ALLOWED SET IS DERIVED FROM `install.sh`, never listed here. The installer renders the launcher
and prints its own usage; taking the set from anywhere else creates a second source of truth that
agrees until one of them changes, which is the failure this repo has hit repeatedly. A subcommand
added to the launcher tomorrow is allowed in the docs the same day, with no edit to this file.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: Where a reader could be given a command to run. Deliberately wide: the two skill trees are as
#: operator-facing as the docs, and the mirrors under `.agents/` are what Codex agents read.
DOC_ROOTS = ("README.md", "docs", ".claude/skills", ".agents/skills", "install.claude.md",
             "install.codex.md", "install.hermes.md", "install.opencode.md", "install.pi.md")

#: A line that RUNS the command, found ONLY INSIDE A FENCED CODE BLOCK.
#:
#: The first version of this matched any line beginning with `aify-comms` and flagged EIGHT
#: passages of prose -- sentences that happened to wrap onto a line starting with the product's
#: name ("aify-comms ships host code whose staleness..."). A gate that fires on paragraphs gets
#: switched off, and takes the real signal with it. A code fence is what actually distinguishes
#: "here is a command to run" from "here is a sentence about the command".
INVOCATION = re.compile(r"^\s*(?:\$\s*)?aify-comms\s+(\S+)")


#: Fences that hold COMMANDS. A bare or ```text fence holds diagrams and quoted output in this repo,
#: and the second version of this gate flagged three of them — including an ASCII system-shape
#: diagram whose line happened to read `aify-comms environment bridge`. Every genuine instruction
#: found by this gate was in one of these.
SHELL_FENCES = ("bash", "sh", "shell", "console", "powershell", "ps1", "zsh")


def commands_in(text: str):
    """Yield (line number, line, first argument) for every `aify-comms` call in a SHELL code fence."""
    fenced = False
    for line_no, line in enumerate(text.splitlines(), 1):
        stripped = line.lstrip()
        if stripped.startswith('```'):
            # Closing a fence carries no tag, so only an opening tag can turn scanning on.
            fenced = not fenced and stripped[3:].strip().lower() in SHELL_FENCES
            continue
        if not fenced:
            continue
        # A TRAILING COMMENT IS NOT AN ARGUMENT. `aify-comms   # refuses` is a doc showing the
        # refusal, which is the behaviour this gate wants documented, not the defect it hunts.
        bare = line.split('#', 1)[0]
        match = INVOCATION.match(bare)
        if match:
            yield line_no, line.strip(), match.group(1)


def allowed_arguments() -> set[str]:
    """The arguments the installed launcher actually accepts, read out of its own usage line."""
    usage = re.search(r"Usage: aify-comms <([^>]+)>", (REPO / "install.sh").read_text(encoding="utf-8"))
    assert usage, "install.sh no longer prints a usage line for aify-comms — this gate cannot derive its set"
    return {part.strip() for part in usage.group(1).split("|")}


def documents() -> list[Path]:
    found: list[Path] = []
    for entry in DOC_ROOTS:
        target = REPO / entry
        if target.is_file():
            found.append(target)
        elif target.is_dir():
            found.extend(p for p in target.rglob("*.md") if "node_modules" not in p.parts)
    return found


class NoDocTeachesARemovedCommand(unittest.TestCase):
    def test_POSITIVE_CONTROL_the_allowed_set_is_read_from_the_installer(self):
        """A set this gate could not read would make every assertion below vacuous."""
        allowed = allowed_arguments()
        self.assertIn("doctor", allowed)
        self.assertIn("--version", allowed)
        self.assertGreaterEqual(len(allowed), 4, f"the usage line parsed to only {allowed}")

    def test_POSITIVE_CONTROL_the_scan_reaches_real_documents(self):
        """An empty corpus passes an `every` check silently — this repo's favourite false green."""
        docs = documents()
        self.assertGreater(len(docs), 20, f"the doc walk found only {len(docs)} files")
        self.assertTrue(any(d.name == "README.md" for d in docs))

    def test_NEGATIVE_CONTROL_it_can_still_say_yes(self):
        """A matcher that cannot fire cannot pass on evidence."""
        fenced = "```bash\naify-comms http://host:8800\n```"
        self.assertEqual([arg for _, _, arg in commands_in(fenced)], ["http://host:8800"])
        # PROSE IS NOT A COMMAND. The first version of this gate flagged eight paragraphs where a
        # sentence wrapped onto a line starting with the product name.
        self.assertEqual(list(commands_in("aify-comms ships host code whose staleness matters.")), [])
        self.assertEqual(list(commands_in("Run `aify-comms doctor` to check.")), [])
        # A TRAILING COMMENT IS NOT AN ARGUMENT.
        self.assertEqual(list(commands_in("```bash\naify-comms    # refuses\n```")), [])
        # A DIAGRAM IS NOT A COMMAND. This exact shape — an ASCII system diagram in a ```text fence —
        # is what the second version of this gate wrongly flagged.
        self.assertEqual(list(commands_in("```text\naify-comms environment bridge\n```")), [])

    def test_no_document_tells_a_reader_to_run_a_removed_command(self):
        allowed = allowed_arguments()
        offenders = []
        for doc in documents():
            text = doc.read_text(encoding="utf-8", errors="replace")
            for line_no, line, argument in commands_in(text):
                if argument not in allowed:
                    offenders.append(f"{doc.relative_to(REPO).as_posix()}:{line_no}: {line}")
        self.assertEqual(
            offenders,
            [],
            "these documents tell a reader to run `aify-comms` with an argument it no longer accepts. "
            "On a current install that exits 2; on a host still running an older launcher it STARTS AN "
            "ENVIRONMENT BRIDGE, which supersedes the running one and reaps its managed workers. The "
            "host tier is aify-env — point the reader there instead of deleting the step.\n  "
            + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
