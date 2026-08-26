"""No tracked text file carries trailing whitespace or a blank line at EOF.

`git diff --check` says so about a range; nothing here said so about the tree. Two candidates in one
session were sent back by the reviewer for exactly this — a trailing space in a test I had just
written, then a blank line at EOF elsewhere in the range — and neither was caught by any suite. A
reviewer spending attention on whitespace is attention not spent on behaviour.

Vendored third-party code is out of scope: diverging from upstream to satisfy a house rule is not a
fix. Empty files are out of scope too; an empty `__init__.py` is idiomatic and adding a newline to it
is churn.

Markdown gets the same rule as everything else, deliberately. Two trailing spaces are a line break in
markdown, so a blanket rule could be a trap — measured before writing this: ZERO lines across 217
tracked markdown files use that form, so the convention here is already "don't", and this records it
rather than discovering it later. Use a backslash or a blank line for a break.
"""

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

TEXT_SUFFIXES = (".py", ".js", ".mjs", ".sh", ".md", ".json", ".yml", ".yaml", ".css", ".in", ".txt")
# Third-party. A house whitespace rule is not a reason to diverge from upstream.
SKIP_PREFIXES = ("service/new_dashboard/vendor/",)


def binary_by_gitattributes() -> set[str]:
    """Files the repo has DECLARED not to be text, asked of git rather than listed here.

    A captured PTY stream is a `.txt` by extension and a byte log in fact: it ends mid-frame with no
    newline and carries the trailing spaces the terminal actually emitted. Three such fixtures landed
    on 2026-08-26 and this gate failed on one of them -- correctly by its own rule, and wrongly about
    the file. They are already marked `-text` in `.gitattributes` so git stops rewriting their line
    endings, and that declaration is the single place the exemption should live: a second hardcoded
    list here would agree with it right up until somebody updated one of the two.
    """
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "--eol"], capture_output=True, text=True, check=True
    ).stdout.split("\n")
    # Each line is `i/<eol> w/<eol> attr/<attrs> \t<path>`; `-text` in the attribute column is git
    # saying it treats the file as binary and will not touch its bytes.
    binary = set()
    for line in out:
        if not line or "\t" not in line:
            continue
        head, path = line.split("\t", 1)
        if "attr/-text" in head:
            binary.add(path.strip())
    return binary


def tracked_text_files() -> list[str]:
    binary = binary_by_gitattributes()
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files"], capture_output=True, text=True, check=True
    ).stdout.split("\n")
    return [
        f for f in out
        if f and f.endswith(TEXT_SUFFIXES) and not f.startswith(SKIP_PREFIXES) and f not in binary
    ]


def offences(text: str) -> list[str]:
    """What `git diff --check` would complain about, for a whole file rather than a diff."""
    if not text.strip():
        return []  # an empty file has nothing to trail
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]  # the trailing newline is not a line — assuming otherwise eats the last one
    found = []
    if any(line != line.rstrip() for line in lines):
        found.append("trailing whitespace")
    if text.endswith("\n\n"):
        found.append("blank line at EOF")
    if not text.endswith("\n"):
        found.append("no newline at EOF")
    return found


def test_the_binary_exemption_is_small_and_specific():
    """A `.gitattributes` slip could exempt the whole tree from this gate and nothing would say so.

    The exemption exists for captured byte logs, so it must stay the size of that idea. Both bounds
    matter: an EMPTY set means the declaration stopped being read and the captures will fail the gate
    again, while a large one means a pattern went too wide and the whitespace rule quietly stopped
    applying to source.
    """
    binary = binary_by_gitattributes()
    assert binary, "no file is declared -text; the captured PTY fixtures will fail this gate"
    assert len(binary) < 20, f"{len(binary)} files are exempt from the whitespace gate: {sorted(binary)}"
    assert all(f.endswith(".txt") for f in binary), f"a non-capture is exempt: {sorted(binary)}"


def test_the_scan_finds_the_files_it_claims_to():
    """Anti-vacuity: a `git ls-files` that returned nothing would make the gate pass by looking at an
    empty set."""
    files = tracked_text_files()
    assert len(files) > 300, f"implausibly few tracked text files: {len(files)}"
    assert "README.md" in files
    assert "install.sh" in files


def test_the_offence_detector_says_both_yes_and_no():
    """It has to be able to fail, and it has to leave the legitimate cases alone."""
    assert offences("clean\n") == []
    assert offences("") == [], "an empty file is not an offence"
    assert offences("\n") == [], "a newline-only file is idiomatic __init__.py"
    assert "trailing whitespace" in offences("bad   \nok\n")
    assert "blank line at EOF" in offences("ok\n\n")
    assert "no newline at EOF" in offences("ok")
    # The bug that ate a comment on the first attempt: a file whose last line has no newline must be
    # examined, not silently dropped.
    assert "trailing whitespace" in offences("ok\nlast line  ")


def test_no_tracked_text_file_offends():
    bad = []
    for name in tracked_text_files():
        try:
            text = (REPO / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        found = offences(text)
        if found:
            bad.append(f"{name}: {', '.join(found)}")
    assert bad == [], (
        "these would fail `git diff --check` for a reviewer:\n  " + "\n  ".join(bad)
    )
