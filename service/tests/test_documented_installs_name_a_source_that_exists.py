"""An install command in a doc names a source someone else can actually install from.

Four documents told an operator to run `npm install -g aify-env` for the client half of the install.
That returns a 404: aify-env is not published. It read as correct on the machine it was written on
because aify-env is `npm link`ed there, which is the shape of this failure -- an install instruction
verified only by the person who never has to run it.

Until the packages are published, the `github:` form is the one that resolves, and the fleet's own
repos are the only source of truth for which names are ours. This test reads them from the aify-comms
side: the pinned dependency in mcp/stdio/package.json names aify-wrapper and the form it is pinned by,
and aify-env is named the same way in the docs it governs.

NAMING THE BROKEN FORM IS ALLOWED WHEN THE WORKING ONE IS IN THE SAME PARAGRAPH -- explaining why
`npm install -g aify-env` fails is exactly how a reader learns not to run it, and a gate that forbade
the words would forbid the explanation. The unit is a paragraph for the same reason the doctor-pointer
gate uses a section: the rule is about whether a reader ends up with a command that works.

DELETE THIS TEST THE DAY THE PACKAGES ARE PUBLISHED. It asserts a workaround, not a design, and the
publish is what retires it -- at which point it fails, loudly, on documentation that has become
correct. That failure is the signal, not a defect.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Our own packages, and how a doc must name them while they are unpublished.
UNPUBLISHED = {"aify-env": "github:zimdin12/aify-env", "aify-wrapper": "github:zimdin12/aify-wrapper"}


def tracked_markdown() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [ROOT / name for name in out.split("\n") if name.strip()]


def command_lines(lines: list[str]):
    """(line number, text) for each line a reader could COPY AND RUN.

    A doc OFFERS a command when it appears as one -- inside a fenced block, or on a line
    that is the command itself, optionally behind a `$`, `>` or `sudo`. A mention inside a
    sentence is DISCUSSION, and this project's docs discuss broken commands on purpose:
    "`npm install -g aify-env` sat in four documents returning 404" is the explanation, not
    the offer. Reviving this gate from a dead regex flagged both, which is how the
    distinction was forced.
    """
    fenced = False
    for index, line in enumerate(lines, 1):
        stripped = line.strip()
        if stripped.startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            yield index, stripped
            continue
        # An unfenced line that IS the command, rather than a sentence containing it.
        bare = stripped.lstrip("$> ").strip()
        bare = bare[5:].strip() if bare.startswith("sudo ") else bare
        if bare.startswith("npm ") or bare.startswith("`npm "):
            yield index, bare.strip("`")


def paragraphs(lines: list[str]):
    """(first line number, text) for each blank-line-separated block."""
    start = None
    for index, line in enumerate(lines):
        if line.strip():
            if start is None:
                start = index
        elif start is not None:
            yield start + 1, chr(10).join(lines[start:index])
            start = None
    if start is not None:
        yield start + 1, chr(10).join(lines[start:])


def test_this_repo_itself_installs_aify_wrapper_by_the_git_form():
    """The positive control: the one place the fleet really does install one of these.

    If this ever reads a bare name, the premise below is wrong and the docs should follow the code
    rather than this test.
    """
    manifest = json.loads((ROOT / "mcp" / "stdio" / "package.json").read_text(encoding="utf-8"))
    pinned = manifest["dependencies"]["aify-wrapper"]
    assert pinned.startswith("github:"), f"aify-wrapper is pinned as {pinned!r}, not by the git form"


def _global_install_pattern(package: str) -> str:
    """The one pattern the gate uses AND its anti-vacuity control checks.

    IT WAS TWO COPIES, AND THE GATE'S COPY COULD NEVER MATCH. A literal BACKSPACE byte sat where
    a word boundary was meant -- `\b` written through a shell heredoc becomes `\x08` -- so the search
    found nothing in any paragraph, every candidate hit `continue`, and `offences` was empty on
    every run. The gate passed because it could not fail.

    ITS CONTROL DID NOT CATCH THAT because the control RETYPED the regex correctly and tested
    the copy. A control has to exercise the subject, not a lookalike, which is this repo's
    own rule about proving the extractor rather than the comparison.
    """
    return rf"npm i(?:nstall)? -g\s+{re.escape(package)}\b"


def test_no_doc_offers_a_global_install_of_an_unpublished_name():
    offences: list[str] = []
    for path in tracked_markdown():
        if not path.exists():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, command in command_lines(lines):
            for package, form in UNPUBLISHED.items():
                # The bare name, not the github: form that contains the same word.
                if not re.search(_global_install_pattern(package), command):
                    continue
                if form in command:
                    continue
                rel = path.relative_to(ROOT).as_posix()
                offences.append(f"{rel}:{number} offers `{command}`, use the `{form}` form")
    assert not offences, "\n".join(
        ["a doc offers an install command that returns 404 for everyone but this machine:", *offences]
    )


def test_the_scan_can_actually_find_one():
    """Anti-vacuity. A regex that matched nothing would pass the test above on any documentation."""
    pattern = _global_install_pattern("aify-env")
    assert re.search(pattern, "npm install -g aify-env"), (
        "the gate's OWN pattern cannot find a bare global install, so the gate above"
        " passes on any documentation")
    assert not re.search(pattern, "npm install -g github:zimdin12/aify-env")

    # AND THE OFFER/DISCUSSION SPLIT, both ways. A fenced command is an offer; the same words
    # inside a sentence are how a doc warns about it, and this gate flagged two such warnings
    # the moment it was revived.
    fenced = list(command_lines(['```bash', 'npm install -g aify-env', '```']))
    assert fenced and any('aify-env' in text for _n, text in fenced), (
        'a fenced install command is not seen as a command, so the gate cannot catch an offer')
    prose = list(command_lines(['It says `npm install -g aify-env` and that returns 404.']))
    assert not prose, (
        'a sentence discussing the command reads as an offer, so explaining a broken install'
        ' would be a violation')
