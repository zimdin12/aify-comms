"""A doc that names a check aify-comms' doctor no longer has must say who owns it now.

v0.6 moved four checks out of `aify-comms doctor`: `wrappers`, `wrapper-current` and `runtimes` to
aify-wrapper (where `aify-wrapper-check` already answered them), and `bridge-terminal` to
`aify-env doctor`. Ten markdown files still told a reader to run `aify-comms doctor` and look for
them. Following that instruction produces no such check and no error -- the check is simply absent
from the report, which reads exactly like a clean run.

THE RULE IS NOT "never mention a moved check". Naming one is often the point: the install guides have
to say where the launcher check went, and docs/TARGET_ARCHITECTURE.md records the move itself. The
rule is that the surrounding PARAGRAPH must name the tool that answers it now, so the pointer
resolves.

THE UNIT IS A SECTION AND THE SCOPE IS TEXT ABOUT THE DOCTOR, and it took two wrong units to get
there. Lines matched everything: `runtimes` is a doctor check AND a field in the capability payload,
so a line rule flagged two blocks of unrelated protocol prose, and a gate that fires on text it has
no business in is one people learn to route around. Paragraphs then matched too little -- the install
guides put `aify-comms doctor` in a fenced block and the expectations in the prose beneath it, which
are different paragraphs, so the guides telling a reader to expect a check that no longer exists all
read as clean. A section holds the command and its expectations together, which is how a reader
consumes them, and it still excludes the payload prose because those sections never mention doctor.

BOTH SETS ARE DERIVED, neither is typed here. The live ids come from the `add(...)` calls in
doctor.js; the retired ids and their new owners come from the forwarding comment above the check
calls -- the same text a human reads when they wonder where a check went. A check moved without
updating that comment leaves this test with nothing to look for, which is why the test also asserts
the comment still describes a non-empty move.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DOCTOR = ROOT / "mcp" / "stdio" / "doctor.js"

# Where a moved check is answered now, and every spelling a doc may legitimately use to name it.
OWNER_SPELLINGS = {
    "aify-wrapper": ("aify-wrapper-check", "aify-wrapper"),
    "aify-env": ("aify-env doctor", "aify-env"),
}


def live_check_ids() -> set[str]:
    """The ids doctor.js actually registers. Several register as `return add(...)`."""
    source = DOCTOR.read_text(encoding="utf-8")
    return set(re.findall(r'\badd\(\s*"([a-z][a-z0-9-]*)"', source))


def retired_checks() -> dict[str, str]:
    """id -> owner key, read out of the forwarding comment above the check calls."""
    source = DOCTOR.read_text(encoding="utf-8")
    note = re.search(
        r"// LAUNCHER AND TERMINAL QUESTIONS ARE NOT THIS TOOL'S\.(.*?)\ncheck",
        source,
        re.S,
    )
    assert note, "the forwarding comment is gone; a moved check now has no recorded owner"
    moved: dict[str, str] = {}
    for line in note.group(1).splitlines():
        owner = next((key for key in OWNER_SPELLINGS if key in line), None)
        if not owner:
            continue
        for check in re.findall(r"`([a-z][a-z0-9-]*)`", line):
            if check not in OWNER_SPELLINGS:
                moved[check] = owner
    # The comment spans lines, so a check named on the line BEFORE its owner belongs to it too.
    lines = note.group(1).splitlines()
    for index, line in enumerate(lines):
        owner = next((key for key in OWNER_SPELLINGS if key in line), None)
        if not owner or index == 0:
            continue
        for check in re.findall(r"`([a-z][a-z0-9-]*)`", lines[index - 1]):
            if check not in OWNER_SPELLINGS:
                moved.setdefault(check, owner)
    return moved


def sections(lines: list[str]):
    """(first line number, last line number, text) for each markdown heading to the next.

    Fenced blocks are tracked so a `#` inside a shell snippet does not split a section -- a comment
    line in an install command would otherwise cut the command away from the prose explaining it,
    which is the exact split this unit exists to avoid.
    """
    starts, fenced = [], False
    for index, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and line.startswith("#"):
            starts.append(index)
    if not starts or starts[0] != 0:
        starts.insert(0, 0)
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        yield start + 1, end, chr(10).join(lines[start:end])


def tracked_markdown() -> list[Path]:
    import subprocess

    out = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [ROOT / name for name in out.split("\n") if name.strip()]


def test_the_forwarding_comment_still_records_a_move():
    """The instrument, proven able to find something before it is trusted to find nothing."""
    moved = retired_checks()
    assert moved, "no moved check found -- this test would pass vacuously against any docs"
    assert set(moved) & {"wrappers", "wrapper-current", "runtimes", "bridge-terminal"}, moved


def test_a_moved_check_is_not_also_a_live_one():
    """A check both registered and described as moved means one of the two is a lie."""
    overlap = live_check_ids() & set(retired_checks())
    assert not overlap, f"registered AND documented as moved: {sorted(overlap)}"


def test_every_doc_line_naming_a_moved_check_names_its_new_owner():
    moved = retired_checks()
    offences: list[str] = []
    for path in tracked_markdown():
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for first, last, section in sections(lines):
            if "doctor" not in section.lower():
                continue
            for check, owner in moved.items():
                if f"`{check}`" not in section:
                    continue
                if any(spelling in section for spelling in OWNER_SPELLINGS[owner]):
                    continue
                rel = path.relative_to(ROOT).as_posix()
                offences.append(f"{rel}:{first}-{last} names `{check}` without naming {owner}")
    assert not offences, "\n".join(["a doc points at a check aify-comms no longer runs:", *offences])
