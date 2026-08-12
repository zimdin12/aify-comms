"""Every borrow shim's target must still exist. A broken one fails at CALL time, not import time.

THE SHAPE OF THIS FAILURE. A borrow shim is a function-scope import:

    def _helper(*a, **k):
        from service.routers.api_v2 import _helper as _impl
        return _impl(*a, **k)

Function-scope is deliberate — it is how a leaf module reaches back to the router without a
module-level import cycle. The cost is that the import is not executed until the shim is CALLED. So
if `_helper` moves out of `api_v2` and this shim is not repointed:

  - the module still imports cleanly;
  - `create_app()` still builds every route;
  - `py_compile` is happy, the undefined-name sweep sees nothing;
  - every test that does not exercise this particular path passes;
  - and the first real caller gets ImportError, in production, on a path that by definition is not
    the one anybody was testing.

There are 200+ shim sites across the routers and reconcilers after the v0.5.x domain phase, and
several helpers moved between modules mid-series (`_spawn_request_to_dict`,
`_clear_console_terminal_binding`, and everything the agents package took). Checking them by reading
imports is exactly the kind of thing that is right until it isn't.

So: resolve every one against the live module. Cheap, and it converts a production-only failure into
a red test.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
SEARCH = ("service/routers/**/*.py", "service/reconcilers/*.py")
BORROW_RE = re.compile(r"from service\.routers\.api_v2 import (\w+)")


def _shim_targets() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for pattern in SEARCH:
        for path in REPO.glob(pattern):
            if path.name == "api_v2.py" or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for match in BORROW_RE.finditer(text):
                found.setdefault(match.group(1), set()).add(path.relative_to(REPO).as_posix())
    return found


class BorrowShimsResolveTests(unittest.TestCase):
    def test_every_borrowed_name_still_exists_on_the_router(self):
        from service.routers import api_v2

        targets = _shim_targets()
        broken = [
            f"{name} — borrowed by {sorted(mods)}"
            for name, mods in sorted(targets.items())
            if not hasattr(api_v2, name)
        ]
        self.assertEqual(
            broken,
            [],
            "A borrow shim points at a name api_v2 no longer has. Because the import is "
            "function-scope, this does NOT fail at import — it fails the first time that code path "
            "runs, in production:\n  " + "\n  ".join(broken)
            + "\nRepoint the shim at the module that owns the name now.",
        )

    def test_the_discovery_mechanism_works(self):
        """A resolve-check over an empty set passes vacuously — so the DISCOVERY is asserted.

        Deliberately NOT `len(targets) > 50`. That was the original form and the reviewer flagged
        it: the whole point of the retirement work is to drive the shim count DOWN, so a population
        floor turns success into a red test and invites someone to "fix" it by lowering the number
        until it means nothing.

        What must never break is the DETECTOR, so that is what is tested — against a synthetic
        sample with a known answer, which stays true whether the repo has two hundred shims or none.
        """
        sample = (
            "def _a(*x, **y):\n"
            "    from service.routers.api_v2 import _a as _impl\n"
            "    return _impl(*x, **y)\n"
            "\n"
            "async def _b(*x, **y):\n"
            "    from service.routers.api_v2 import _b as _impl\n"
            "    return await _impl(*x, **y)\n"
            "\n"
            "# from service.routers.api_v2 import _not_a_real_one\n"
        )
        found = set(BORROW_RE.findall(sample))
        self.assertEqual(
            found,
            {"_a", "_b", "_not_a_real_one"},
            "the borrow-shim pattern no longer recognises the shim shapes this repo uses",
        )

    def test_the_repo_population_is_reported_not_asserted(self):
        """Visibility without a brittle floor.

        The count is worth SEEING as the retirement proceeds — it is the debt burning down — but it
        is not a correctness property, so it is not an assertion. The only thing asserted is the
        weakest true statement: if any shim exists, the scan found at least one.
        """
        targets = _shim_targets()
        sites = sum(len(v) for v in targets.values())
        print(f"\n  borrow shims remaining: {len(targets)} names across {sites} sites")
        has_any = any(
            "from service.routers.api_v2 import" in path.read_text(encoding="utf-8", errors="replace")
            for pattern in SEARCH
            for path in REPO.glob(pattern)
            if path.name != "api_v2.py" and "__pycache__" not in path.parts
        )
        self.assertEqual(
            bool(targets), has_any,
            "shims exist in the tree but the scan found none (or vice versa)",
        )

    def test_no_shim_points_at_a_module_that_would_cycle(self):
        """Module-level borrowing from the router is the cycle this pattern exists to avoid.

        A shim is safe because it imports inside a function. A module-level
        `from service.routers.api_v2 import X` in a router/reconciler leaf is a different thing and
        would be an import cycle waiting for load order to change.
        """
        offenders = []
        for pattern in SEARCH:
            for path in REPO.glob(pattern):
                if path.name == "api_v2.py" or "__pycache__" in path.parts:
                    continue
                for lineno, line in enumerate(
                    path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
                ):
                    if line.startswith("from service.routers.api_v2 import"):
                        offenders.append(f"{path.relative_to(REPO).as_posix()}:{lineno}")
        self.assertEqual(
            offenders,
            [],
            "A leaf module imports the router at MODULE level. Borrow shims must keep the import "
            "inside the function:\n  " + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
