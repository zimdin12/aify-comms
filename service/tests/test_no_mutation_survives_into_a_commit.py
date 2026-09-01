"""A mutation must not survive into a commit.

WHY THIS EXISTS, with a date. `ec67d24b` (2026-08-30 20:40) committed

    -    app.add_middleware(CrossSiteBrowserMiddleware, allowed_origins=config.cors_origins)
    +    pass  # MUTATED

inside a DOCS commit, and `9b920070` reverted it 86 seconds later. For those 86 seconds `main` carried
a tree with the cross-site browser guard not installed -- the guard `d6c3646a` had added eighteen
hours earlier to close the 2026-06-28 rebinding hole. Anyone building from that sha runs without it.

IT WAS NOT A COVERAGE GAP. `test_a_page_on_another_site_cannot_drive_this_service.py` already drove
`create_app()` and asserted 403, in that commit's own tree; re-applying the mutation today fails
exactly `test_the_real_app_refuses_a_cross_site_page` and `test_it_runs_OUTSIDE_the_api_key_check`.
So the suite would have been red and the commit was made anyway. A test cannot defend against not
being run, which is why the defence has to be a property of the TREE rather than of the suite's
verdict -- this gate fails on the marker itself, so a `git add -A` cannot carry one past.

WHAT IT DOES AND DOES NOT CATCH, said plainly so nobody reads more into a green run. It catches a
LABELLED mutation: the marker a careful person writes so they can find their own edit again. An
unlabelled mutation is invisible to it and always will be. That is a narrow guarantee, and it is the
right one -- the person who labels their mutation is exactly the person whose label is the thing that
survives, and mutation testing is standing practice here rather than an occasional exercise.

THE PATTERN IS THE COMMENT MARKER, NOT THE WORD. Four files at HEAD use "MUTATED" in prose --
"`spec_id` IS RETURNED RATHER THAN MUTATED" -- and a word-match would fire on every one of them, which
is how a gate gets weakened into an exemption list. Requiring a comment introducer means the prose is
not a special case that had to be excused; it simply is not a marker, and the negative control below
pins that against the real lines rather than an invented example.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

SERVICE = Path(__file__).resolve().parent.parent
REPO = SERVICE.parent

#: Pruned at the DIRECTORY level, matching the oversized-file gate so both judge one population.
SKIP_DIRS = frozenset(
    {"__pycache__", "tests", "node_modules", ".git", "fixtures", ".pytest_cache", ".venv", "venv"}
)

EXTENSIONS = (".py", ".js", ".mjs")

#: A comment introducer, then the marker. `#` covers Python and shell, `//` covers JS.
MARKER = re.compile(r"(?:#|//)\s*MUTATED\b")


def _source_files(root: Path = REPO, skip=SKIP_DIRS):
    """Every non-test source file in the repo, pruned at the directory level.

    BOTH LANGUAGES FROM ONE PLACE, unlike the oversized gate's pair. A mutation is applied to whatever
    file is under test, and the workflow requires all five suites before a commit, so one scan that
    sees the whole tree is enough -- and a second copy of this rule is the drift this repo keeps
    paying for.
    """
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in skip)
        for name in sorted(filenames):
            if name.endswith(EXTENSIONS) and not name.startswith("test_") and ".test." not in name:
                yield Path(dirpath) / name


def _offences():
    found = []
    for path in _source_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if MARKER.search(line):
                found.append(f"{path.relative_to(REPO).as_posix()}:{number}: {line.strip()}")
    return found


def test_the_scan_sees_a_real_population() -> None:
    """POSITIVE CONTROL on the WALK. A scan that reached no files would report clean forever."""
    files = list(_source_files())
    assert len(files) > 100, f"the walk found only {len(files)} files -- it is broken, not clean"
    names = {p.name for p in files}
    assert "main.py" in names, "the walk missed service/main.py, the file this gate exists for"
    assert any(p.suffix in {".js", ".mjs"} for p in files), "the walk found no JS at all"


def test_the_pattern_matches_the_marker_that_actually_escaped() -> None:
    """POSITIVE CONTROL on the PATTERN, using the exact line `ec67d24b` committed."""
    assert MARKER.search("    pass  # MUTATED")
    assert MARKER.search("  // MUTATED")
    assert MARKER.search("const x = 1;  // MUTATED: was 2")


def test_the_pattern_does_not_fire_on_prose() -> None:
    """NEGATIVE CONTROL, against the REAL sentences at HEAD rather than an invented one.

    A pattern that matched these would have to be excused with a list, and a list is where a gate
    stops being a gate.
    """
    for line in (
        "`cancelled_queued` IS RETURNED RATHER THAN MUTATED so the caller can report it. After",
        "`spec_id` IS RETURNED RATHER THAN MUTATED: the caller records it on the agent session",
        "        `runtime_state` and `switch_warnings` are MUTATED in place rather than returned.",
        "# the value is mutated downstream",
    ):
        assert not MARKER.search(line), f"the pattern fired on prose: {line!r}"


def test_no_tracked_source_file_carries_a_mutation_marker() -> None:
    offences = _offences()
    assert offences == [], (
        "a mutation marker survived into the tree:\n  "
        + "\n  ".join(offences)
        + "\n\nRestore the original line. `ec67d24b` shipped one of these to main and left the "
        "cross-site browser guard uninstalled for 86 seconds of history."
    )
