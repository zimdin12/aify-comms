"""No product source decides where something IS from a path typed into it.

THE RULE, from the operator on 2026-09-16: "we should never have C:/ paths. we never know where user
installs anything. everything should be dynamic in that sense. agent who installs should fill the
dynamic gaps based on the system, mb C:/ could be as example."

WHAT IT CAUGHT THE DAY IT WAS WRITTEN. `environmentStartCommand` handed every host that read the
dashboard `cd /d C:\\Docker` (or `cd /mnt/c/Docker`) whenever an environment advertised no root -- the
directories this project happens to live in on the machine it was written on. `codexSpawnCwd` fell back
to `C:\\`, `defaultCodexCommand` to `C:\\Windows`, and a measurement script named one checkout.

CODE, NOT PROSE. A comment explaining `C:\\Users\\...` is how these files earn their keep, and the
distinction is what `scripts/comment_spans.py` exists for -- Python answered by `tokenize` and `ast`,
JavaScript by the scanner that module has already had corrected twice. A gate that read whole lines
would have failed on its own explanation.

AN EXAMPLE SHOWN TO A HUMAN IS ALLOWED, and says so on its line: `// example path`. Those are counted
and ratcheted, so one more arrives as a decision rather than as a habit.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "scripts"))

from comment_spans import js_comment_spans, python_comment_spans  # noqa: E402  (the repo's own classifier)

#: A drive-letter path (`C:/x`, `d:\x`) or this machine's WSL mount. Both name a place on ONE host.
HOST_PATH = re.compile(r"(?:(?<![A-Za-z0-9])[A-Za-z]:[\\/])|(?:/mnt/[a-z]/)")

#: The marker a line carries when its path is shown to a person rather than used.
EXAMPLE = "example path"

#: Measured 2026-09-16, and both are read by a person rather than used: the refusal that tells an operator
#: which cwd form codex takes, and the hint in an empty roots box. It may only go DOWN.
EXAMPLE_CEILING = 2

#: `docs` holds the probe scripts a review ran on one machine, kept as the evidence for what it found.
#: They are not product source and re-running one elsewhere is not a thing anybody does.
SKIP_DIRS = {"node_modules", "__pycache__", ".git", ".pytest_cache", ".venv", "venv", "tests", "fixtures", ".monitor", "docs"}
SUFFIXES = {".py", ".js", ".mjs"}


def _product_files() -> list[Path]:
    found = []
    for path in REPO.rglob("*"):
        if not path.is_file() or path.suffix not in SUFFIXES:
            continue
        if set(path.relative_to(REPO).parts) & SKIP_DIRS:
            continue
        if path.name.startswith("test_") or ".test." in path.name:
            continue
        found.append(path)
    return sorted(found)


def _code_regions(text: str, suffix: str) -> str:
    """The same text with every comment blanked, so only code is searched."""
    blanked = list(text)
    spans = python_comment_spans(text) if suffix == ".py" else js_comment_spans(text)
    for start, end in spans:
        for index in range(start, min(end, len(blanked))):
            if blanked[index] != "\n":
                blanked[index] = " "
    return "".join(blanked)


class NoSourceBakesInAHostPath(unittest.TestCase):
    def test_no_product_file_names_a_drive_or_a_wsl_mount_in_code(self) -> None:
        files = _product_files()
        self.assertGreater(len(files), 200, "the walk found almost nothing, so it proves almost nothing")

        offenders: list[str] = []
        examples: list[str] = []
        for path in files:
            text = path.read_text(encoding="utf-8", errors="replace")
            if not HOST_PATH.search(text):
                continue
            code = _code_regions(text, path.suffix)
            for number, line in enumerate(code.splitlines(), start=1):
                if not HOST_PATH.search(line):
                    continue
                lines = text.splitlines()
                whole = lines[number - 1]
                # The marker may sit on the line or the one above it, because a long line's note goes above.
                marked = EXAMPLE in whole.lower() or (number > 1 and EXAMPLE in lines[number - 2].lower())
                where = f"{path.relative_to(REPO).as_posix()}:{number}: {whole.strip()[:120]}"
                (examples if marked else offenders).append(where)

        self.assertEqual(
            offenders,
            [],
            "these decide a location from a path typed into the source; read it from the system instead "
            "(an environment variable, the root this process runs on, the file's own location), or mark it "
            f"`{EXAMPLE}` if a person is meant to read it:\n  " + "\n  ".join(offenders),
        )
        self.assertLessEqual(
            len(examples),
            EXAMPLE_CEILING,
            f"more example paths than the {EXAMPLE_CEILING} measured; each is a decision:\n  " + "\n  ".join(examples),
        )
        self.assertEqual(
            len(examples),
            EXAMPLE_CEILING,
            f"only {len(examples)} example path(s) left, so the ceiling has slack: lower EXAMPLE_CEILING to match",
        )

    def test_the_scan_reads_code_and_not_the_comment_beside_it(self) -> None:
        """POSITIVE AND NEGATIVE CONTROL, in one run: a probe that cannot fail cannot pass."""
        for suffix, prose, code in ((".py", '# a note about C:/x\n', 'ROOT = "C:/x"\n'), (".js", "// a note about C:/x\n", 'const root = "C:/x";\n')):
            self.assertNotRegex(_code_regions(prose, suffix), HOST_PATH, f"{suffix}: a comment was searched as code")
            self.assertRegex(_code_regions(code, suffix), HOST_PATH, f"{suffix}: real code was not searched")
        self.assertRegex("D:\\work", HOST_PATH, "another drive is the same defect")
        self.assertRegex("/mnt/c/Docker", HOST_PATH, "the WSL mount names one host too")
        for innocent in ("http://localhost:8800", "a ratio of 1:2", "time 10:30"):
            self.assertNotRegex(innocent, HOST_PATH, f"{innocent} is not a host path")


if __name__ == "__main__":
    unittest.main()
