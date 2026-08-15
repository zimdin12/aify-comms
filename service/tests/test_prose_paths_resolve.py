"""Every repo-relative file path written in a comment or docstring must point at a real file.

This repo navigates by prose. Module docstrings say where a helper came from, comments say which
module owns a constant, and the v0.5.x decomposition has left hundreds of these trails behind — 629
path references across service/, mcp/ and scripts/. A trail is only worth writing if it can be
followed, and nothing was checking that the destinations existed.

FOUND BY THE THING IT NOW PREVENTS. `api_core/reply_expectation.py` named a module that was never
created, calling it "the extraction this unblocked". The claim was plausible and adjacent to true —
the extraction is `api_core/console_input_queue.py` — and nothing in three suites could tell. The
dead name is not written here, because this gate would then flag its own docstring; that is not an
argument for exempting the gate from its own scan (an earlier gate did that and quietly exonerated
the very modules it tracked), it is the rule working. It is the same class as
the `service/db.py` comment that claimed to be "kept in sync with service/routers/api_v2.py
_NATIVE_MANAGED_RUNTIMES", a file that has declared nothing since v0.5.4.

NO ALLOWLIST, DELIBERATELY, and it took three measurements to earn that. The first scan reported 82
failures because prose abbreviates — a docstring writes `api_core/liveness.py` for what is really
`service/api_core/liveness.py`. The second reported 7, six of them `/tmp/x.py` inside `docker exec`
lines, which are container paths and not repo-relative at all. Resolving the abbreviation and
excluding absolute paths leaves ZERO. A gate with no exemptions cannot rot into a list nobody
re-reads, so both rules are encoded as predicates rather than as entries.

WHAT THIS DOES NOT CHECK: whether a SYMBOL named beside a path still exists. That check was built and
withdrawn, and the reason is recorded in `test_the_symbol_half_of_this_gate_was_measured_and_rejected`
below so it is not re-derived.
"""

from __future__ import annotations

import ast
import re
import unittest
from functools import lru_cache
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent

#: A repo-relative path reference. The lookbehind keeps ABSOLUTE paths out: `/tmp/repair.py` in a
#: `docker exec` line is a path inside the CONTAINER, and an earlier version silently ate the leading
#: slash and reported six of them as broken repo references.
PATH_RE = re.compile(r"(?<![/\w.-])((?:[\w.-]+/)+[\w.-]+\.(?:py|js|mjs))\b")

#: Prose abbreviates. `api_core/liveness.py` means `service/api_core/liveness.py`, and treating that
#: as a stale pointer produced 82 false positives — enough noise to get a gate switched off. These
#: are the roots a bare reference is resolved against, in order.
PREFIXES = ("", "service/", "mcp/", "mcp/stdio/", "service/routers/", "service/new_dashboard/")

ROOTS = ("service", "mcp", "scripts")


@lru_cache(maxsize=None)
def resolve(ref: str) -> Path | None:
    """The file a prose reference names, or None if no root makes it exist."""
    for prefix in PREFIXES:
        candidate = REPO / (prefix + ref)
        if candidate.exists():
            return candidate
    return None


def prose_of(path: Path) -> list[tuple[int, str]]:
    """(line, text) for every comment and docstring in a Python file."""
    source = path.read_text(encoding="utf-8", errors="replace")
    out = [
        (i, line.strip())
        for i, line in enumerate(source.splitlines(), 1)
        if line.strip().startswith("#")
    ]
    try:
        tree = ast.parse(source)
    except SyntaxError:  # pragma: no cover - another gate's failure
        return out
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                out.append((getattr(node, "lineno", 1), doc))
    return out


def sources():
    for root in ROOTS:
        base = REPO / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            if "__pycache__" not in path.parts:
                yield path


def references() -> list[tuple[str, int, str]]:
    """(file, line, reference) for every repo-relative path mentioned in prose."""
    out = []
    for path in sources():
        rel = path.relative_to(REPO).as_posix()
        for lineno, text in prose_of(path):
            for match in PATH_RE.finditer(text):
                out.append((rel, lineno, match.group(1)))
    return out


def unresolvable() -> list[tuple[str, int, str]]:
    return [(f, n, ref) for f, n, ref in references() if resolve(ref) is None]


class ProsePathsResolveTests(unittest.TestCase):
    def test_every_path_named_in_prose_exists(self):
        broken = unresolvable()
        self.assertEqual(
            [], broken,
            "prose names files that do not exist. A trail that cannot be followed is worse than no "
            "trail, because it reads as governance:\n  "
            + "\n  ".join(f"{f}:{n}  ->  {ref}" for f, n, ref in broken),
        )

    def test_the_scan_reaches_a_plausible_amount_of_prose(self):
        """Anti-vacuity: a regex that stopped matching would report zero broken paths forever.

        The floor sits well below the measured 629 rather than at it — pinned to the exact reading,
        this fails on the next docstring edited, and a gate that cries wolf gets its number raised
        instead of read.
        """
        refs = references()
        self.assertGreater(len(refs), 300, f"only {len(refs)} path references found in prose")
        self.assertGreater(
            len({f for f, _, _ in refs}), 50, "references should span many files")

    def test_the_abbreviation_convention_resolves(self):
        """`api_core/liveness.py` means `service/api_core/liveness.py`. 82 false positives said so."""
        self.assertIsNotNone(resolve("api_core/runtime.py"), "bare api_core/ must resolve")
        self.assertIsNotNone(resolve("service/api_core/runtime.py"), "full path must resolve")
        self.assertIsNone(resolve("api_core/definitely_not_here.py"), "a real miss must stay a miss")

    def test_absolute_container_paths_are_not_treated_as_repo_paths(self):
        """`docker exec ... python /tmp/repair_shared_crlf.py` names a path inside the CONTAINER."""
        found = [m.group(1) for m in PATH_RE.finditer("docker exec x python /tmp/repair.py --apply")]
        self.assertEqual([], found, "an absolute path must not be read as a repo-relative reference")
        self.assertEqual(
            ["service/db.py"],
            [m.group(1) for m in PATH_RE.finditer("see service/db.py for the schema")],
        )

    def test_the_detector_reports_a_SYNTHETIC_broken_path(self):
        """Scanning a clean tree proves nothing. A regex matching nothing looks identical to a pass."""
        text = "see service/api_core/no_such_module.py for the rest"
        found = [m.group(1) for m in PATH_RE.finditer(text)]
        self.assertEqual(["service/api_core/no_such_module.py"], found)
        self.assertIsNone(resolve(found[0]), "the detector must fail to resolve a made-up path")

    def test_the_symbol_half_of_this_gate_was_measured_and_rejected(self):
        """MEASURED NEGATIVE RESULT, recorded so it is not rebuilt. Read before extending this file.

        The defect that motivated all of this was a SYMBOL pointer, not a path one: `service/db.py`
        claimed to be kept in sync with `service/routers/api_v2.py _NATIVE_MANAGED_RUNTIMES` while
        that module declared nothing. So the obvious extension is to check the name written beside a
        path. It does not work, and the failure is not fixable by tuning:

          * "the token after a path is a symbol" reads ordinary English as a pointer — `... in`,
            `... owns`, `... rather` — 386 false positives.
          * "only CONSTANT_CASE counts" collides head-on with the house style, which uses ALL-CAPS
            for emphasis everywhere: `config.py INSERTS a row`, `dead_imports.py AND the gate`.
          * "require backticks" would be clean, but the motivating defect had none, so the one case
            this was built for is exactly the one it would miss.

        Adjacency cannot separate a pointer from prose in either direction. The PATH half has no such
        ambiguity — a path either resolves or it does not — which is why only that half shipped.
        """
        self.assertTrue(True)


if __name__ == "__main__":
    unittest.main()
