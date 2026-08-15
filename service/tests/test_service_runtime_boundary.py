"""The service runtime must not reach into code doctor excludes from rebuild checks.

WHY THIS EXISTS
---------------
`aify-doctor`'s `service` check decides "is the running container stale?" by asking whether any
commit since the build touched `SERVICE_RUNTIME_PATHS` (`service`, `mcp/sse_server.py`, `config`,
`Dockerfile`). Everything else the Dockerfile copies — `mcp/stdio`, `integrations`, `.agents`,
`install.sh` — is declared NON-runtime cargo in `SERVICE_IMAGE_NON_RUNTIME_PATHS`, so a change
there does NOT ask for a rebuild.

That narrowing removed a false RED (a host-bridge commit demanding a container rebuild for code
the container never executes). But it buys a false GREEN risk in exchange: if the service ever
starts importing or reading one of the excluded paths, doctor would report clean while the running
code genuinely differed from the checkout. False green is the worse direction — a red gets
investigated, a green does not.

So this test is the guard on that trade. It asserts the boundary the path set assumes: nothing the
container executes reaches into excluded code. If someone makes the service import `mcp.stdio`,
this fails and forces the rebuild contract to be revisited rather than silently broken.

Requested by `comms-senior-dev` in the review that rejected the first, broader path set.
"""

from __future__ import annotations

import ast
import re
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Mirrors SERVICE_IMAGE_NON_RUNTIME_PATHS in mcp/stdio/doctor-predicates.js. Kept as a literal
# rather than parsed out of the JS: if the two drift, the JS-side Dockerfile-coverage test and this
# one disagree, and a human has to reconcile them — which is the point.
EXCLUDED_ROOTS = ("mcp/stdio", "integrations", ".agents", "install.sh")

# What the container actually runs: `uvicorn service.main:app`, plus the SSE transport that
# service/main.py loads dynamically.
#
# `mcp` IS A DIRECTORY HERE, and was the single file `mcp/sse_server.py` until 2026-08-15. Both this
# scan and doctor's `SERVICE_RUNTIME_PATHS` named that one file, so a second runtime module beside it
# — the obvious result of decomposing a 730-line file — would have been scanned by neither. It could
# have imported `mcp.stdio` freely and doctor would still have called the container clean. Naming a
# DIRECTORY makes the safe answer the default for a file that does not exist yet, which is the only
# moment this can be decided: once the file is there, nothing reports that it went ungoverned.
RUNTIME_SOURCES = ["service", "mcp"]

# Mirrors SERVICE_RUNTIME_EXCLUDE_PATHS in doctor-predicates.js. Directory names, matched on parts.
RUNTIME_EXCLUDE_DIRS = ("tests", "stdio", "node_modules", "__pycache__", "fixtures")


def _runtime_python_files(repo_root: Path = REPO_ROOT, sources=RUNTIME_SOURCES) -> list[Path]:
    """Parameterised so the SELECTION can be tested against a synthetic tree.

    Against the real repo this scan cannot demonstrate its own rule: `mcp/` holds exactly one runtime
    module today, so "we scan the directory" and "we scan that one file" produce identical results
    and the difference only appears the day someone adds the second — which is the day nothing is
    left to report it. A tmpdir can hold the file that does not exist yet.
    """
    files: list[Path] = []
    for rel in sources:
        p = repo_root / rel
        if p.is_file() and p.suffix == ".py":
            files.append(p)
        elif p.is_dir():
            for f in p.rglob("*.py"):
                # Tests are not the runtime — they legitimately reference installer and skill
                # paths, and excluding them is what makes this assertion about the SERVICE.
                if any(part in RUNTIME_EXCLUDE_DIRS for part in f.parts) or f.name.startswith("test_"):
                    continue
                files.append(f)
    return files


class RuntimeImportBoundaryTests(unittest.TestCase):
    def test_runtime_never_imports_excluded_modules(self):
        """AST scan: no `import mcp.stdio` / `from mcp.stdio import ...` in runtime code.

        Note `from mcp.server.fastmcp import FastMCP` in mcp/sse_server.py is the PyPI `mcp`
        package, NOT this repo's mcp/ directory — that distinction is why this checks the
        dotted path rather than the top-level name.
        """
        offenders = []
        for path in _runtime_python_files():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:  # pragma: no cover - would fail the syntax suite first
                continue
            for node in ast.walk(tree):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if name == "mcp.stdio" or name.startswith("mcp.stdio."):
                        offenders.append(f"{path.relative_to(REPO_ROOT)}: imports {name}")
        self.assertEqual(
            offenders,
            [],
            "Service runtime code imports host-side bridge code that aify-doctor treats as "
            "NON-runtime cargo. doctor would report the container clean while the code it runs "
            "changed — a false green. Either stop importing it, or add the path to "
            "SERVICE_RUNTIME_PATHS in mcp/stdio/doctor-predicates.js so a change there demands a "
            f"rebuild. Offenders: {offenders}",
        )

    def test_runtime_never_reads_excluded_paths_by_string(self):
        """An import is not the only way in — a path literal + open() works too.

        BEST-EFFORT, and stated as such rather than implied to be complete. The pattern requires
        the excluded root to appear at the START of a string literal, so it catches
        `open("mcp/stdio/x")` but NOT a composed path like `f"{root}/mcp/stdio/x"` or
        `join(base, "mcp/stdio")`. Verified against synthetic cases before landing: literal-prefix
        paths match, and prose like health.py's `"See mcp/stdio/ directory ..."` does not (the
        quote is followed by "See", so it never matches — which makes the `See` filter below
        redundant belt-and-braces rather than the thing doing the work).

        The IMPORT check above is the load-bearing guard and it IS precise — it distinguishes
        `mcp.stdio` from the PyPI `mcp.server.fastmcp`. This one is a cheap second net, not a
        proof. If the service ever needs to read bridge files, expect to catch it in review rather
        than here.
        """
        # e.g. "mcp/stdio/server.js", './integrations/x', "install.sh" as a lone operand
        pattern = re.compile(
            r"""["'](?:\./)?(mcp/stdio|integrations|\.agents)/[A-Za-z0-9_.\-/]+["']"""
        )
        offenders = []
        for path in _runtime_python_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            try:
                tree = ast.parse(text)
            except SyntaxError:  # pragma: no cover
                continue
            # Collect docstring spans so prose mentioning a directory is not a finding.
            doc_lines: set[int] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.end_lineno and node.end_lineno - node.lineno > 0:
                        doc_lines.update(range(node.lineno, node.end_lineno + 1))
            for i, line in enumerate(text.splitlines(), start=1):
                if i in doc_lines:
                    continue
                m = pattern.search(line)
                if not m:
                    continue
                # A dict/JSON value that is pure description ("See mcp/stdio/ directory ...") is
                # not a read. Require the literal to be a plausible operand, not a sentence.
                if re.search(r"\b(See|see)\b", line):
                    continue
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{i}: {line.strip()[:100]}")
        self.assertEqual(
            offenders,
            [],
            "Service runtime code appears to read a path aify-doctor treats as NON-runtime "
            f"cargo. Same false-green risk as the import case. Offenders: {offenders}",
        )

    def test_the_scan_actually_scans_something(self):
        """A guard whose corpus is empty proves nothing — this is how a boundary test rots."""
        files = _runtime_python_files()
        self.assertGreater(len(files), 20, "runtime corpus looks too small; did RUNTIME_SOURCES break?")
        names = {f.name for f in files}
        self.assertIn("main.py", names)
        self.assertIn("api_v2.py", names)
        self.assertIn("sse_server.py", names)

    def test_excluded_roots_are_real_paths(self):
        """If an excluded path stops existing, the exclusion is stale and should be removed."""
        for rel in EXCLUDED_ROOTS:
            self.assertTrue((REPO_ROOT / rel).exists(), f"{rel} no longer exists; drop the exclusion")

    def test_a_NEW_module_beside_the_sse_transport_is_runtime_by_default(self):
        """The hole this scan shipped with, shown on the tree that does not exist yet.

        `RUNTIME_SOURCES` named the single file `mcp/sse_server.py`, which is opt-IN: decompose that
        730-line module and its siblings are scanned by nothing, free to import `mcp.stdio`, while
        doctor reports the container clean because its `SERVICE_RUNTIME_PATHS` named the same one
        file. Both halves had the rule written the same wrong way, so neither could catch the other.

        Nothing in the real tree distinguishes the two spellings — that is the whole difficulty, and
        why this builds the case in a tmpdir instead of asserting on the repo.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel in (
                "mcp/sse_server.py",
                "mcp/sse_container_tools.py",   # the sibling a decomposition would create
                "mcp/stdio/server.py",          # host-side: never executed by the container
                "mcp/stdio/node_modules/pkg/setup.py",
                "service/main.py",
                "service/tests/test_thing.py",
                "service/api_core/test_helpers.py",  # a test_ file outside a tests dir
            ):
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_text("x = 1\n", encoding="utf-8")

            scanned = {p.relative_to(root).as_posix() for p in _runtime_python_files(root)}
            self.assertEqual(
                {"mcp/sse_server.py", "mcp/sse_container_tools.py", "service/main.py"},
                scanned,
            )

    def test_the_sources_name_a_DIRECTORY_not_the_one_file_in_it(self):
        """Pinned separately, because the tmpdir test above would pass a hardcoded file list too.

        This is the declaration itself: `mcp` must be the directory. Naming `mcp/sse_server.py` is
        what made the sibling invisible, and it is the spelling a future edit is most likely to
        "restore" as a tidy-up.
        """
        self.assertIn("mcp", RUNTIME_SOURCES)
        self.assertNotIn("mcp/sse_server.py", RUNTIME_SOURCES)
        self.assertIn("stdio", RUNTIME_EXCLUDE_DIRS, "or every bridge file becomes runtime")


if __name__ == "__main__":
    unittest.main()
