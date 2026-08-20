"""Every relative link in a tracked markdown file points at something that exists.

A link is a promise that you can follow it. Unlike a backticked path — which is often a deliberate
mention of a file that was removed, and which this repo's prose is full of on purpose — a relative
link has no legitimate dead case. That difference is why this gate exists and a path gate does not:
measured 2026-08-21, dead backticked paths number 100 across 41 files, nearly all historical plans
describing the world as it was, while dead LINKS number zero across all 217 files.

Two were fixed today, both the same shape: the v0.6 plans named files in aify-wrapper and aify-env as
if they were local, so a reader in this checkout followed them nowhere. A three-repo split invites
exactly that — the path is obvious to whoever wrote it and resolves nowhere for everyone else.

External URLs, in-page anchors and mailto: are not this test's business; only paths on disk are.
"""

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# [text](target) where target is not a URL, an anchor, or a mail link. The `#fragment` is stripped:
# README.md#setup is a link to README.md as far as the filesystem is concerned.
LINK = re.compile(r"\[[^\]]*\]\((?!https?:|#|mailto:)([^)#]+)")


def tracked_markdown() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "*.md"], capture_output=True, text=True, check=True
    ).stdout.split()
    return out


def dead_links_in(text: str, doc: Path) -> list[str]:
    return [
        target for target in LINK.findall(text)
        if not (doc.parent / target.strip()).exists()
    ]


def test_the_scan_finds_the_files_it_claims_to_scan():
    """Anti-vacuity. A `git ls-files` that returned nothing would make the gate below pass by looking
    at an empty set, which is the failure this repo keeps re-learning."""
    files = tracked_markdown()
    assert len(files) > 100, f"implausibly few markdown files: {len(files)}"
    assert "README.md" in files
    assert "CLAUDE.md" in files


def test_the_link_pattern_can_say_both_yes_and_no(tmp_path):
    """A pattern that matched nothing, or matched everything, would give the same green either way."""
    doc = tmp_path / "probe.md"
    doc.write_text("x", encoding="utf-8")

    real = tmp_path / "exists.md"
    real.write_text("y", encoding="utf-8")

    assert dead_links_in("[gone](no-such-file.md)", doc) == ["no-such-file.md"]
    assert dead_links_in("[here](exists.md)", doc) == []
    # Not this test's business, and each must be ignored rather than reported dead.
    assert dead_links_in("[web](https://example.com/x.md)", doc) == []
    assert dead_links_in("[anchor](#setup)", doc) == []
    assert dead_links_in("[mail](mailto:someone@example.com)", doc) == []
    # A fragment on a real file is still that file.
    assert dead_links_in("[section](exists.md#part)", doc) == []


def test_every_relative_link_in_tracked_markdown_resolves():
    broken = []
    for name in tracked_markdown():
        doc = REPO / name
        try:
            text = doc.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        broken.extend(f"{name} -> {target}" for target in dead_links_in(text, doc))

    assert broken == [], (
        "these links promise a file that is not there. A trail that cannot be followed is worse than "
        f"no trail, because it reads as governance:\n  " + "\n  ".join(broken)
    )
