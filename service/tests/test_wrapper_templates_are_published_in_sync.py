"""The wrapper templates exist in two repos. This fails when they drift apart.

`wrappers/*.sh.in` here, and the same four files in the standalone package at
https://github.com/zimdin12/aify-wrapper. Today they are byte-identical copies: aify-comms renders
its own, and the package renders the same text for a host that wants launchers without the service.
The operator's decision (2026-08-19) is that the package becomes the published thing and aify-comms
consumes it; until that lands, there are two sources of truth for one artifact.

WHAT GOES WRONG WITHOUT THIS. Somebody fixes a wrapper here, the package keeps the old text, and the
published launcher silently ships behaviour that was already corrected. Nothing in either repo would
notice, because each is internally consistent — which is exactly the shape of every deploy failure
this project has recorded: the files are fine, the copy that runs is old.

WHAT THIS DOES, and its limit. It records a hash per template. Editing a template here fails this
test, and the failure says to re-sync the package and update the hash IN THE SAME CHANGE. It cannot
reach across to the other repo, so it does not prove they match — it proves nobody changed one
without being told to change the other, which is the part a test can enforce. Delete this file the
day aify-comms consumes the package instead of copying it, because then the duplication is gone and
so is the reason.
"""

from __future__ import annotations

import hashlib
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WRAPPERS = REPO / "wrappers"

PACKAGE_URL = "https://github.com/zimdin12/aify-wrapper"

#: sha256 of each template, MEASURED 2026-08-20 at aify-wrapper commit 4640d1e.
#: Changing a template means updating the hash here AND re-syncing the package in the same change.
PUBLISHED = {
    "claude-aify.sh.in": "5ff08fa70243cb8535a0fd81fcd2c032a4f4c1ec0d6ca300b3bbe2452dbbd17a",
    "codex-aify.sh.in": "7e4d0480fbbded52440f0f90b170fc5b24e8648ae9f7c28ab50e72d10f1d25bf",
    "hermes-aify.sh.in": "aea6fcc2a43d97c72f1c54b093ea2b4df23d5ebc0b3852ad4af69859af87455d",
    "pi-aify.sh.in": "cdfc2e3789c6e60c363d61b531b25b1ebf3314c8108bff914e6808ca046719f1",
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class WrapperTemplatesArePublishedInSync(unittest.TestCase):
    def test_the_scan_finds_the_templates_at_all(self):
        """Without this, a moved directory turns every assertion below into a vacuous pass."""
        found = sorted(p.name for p in WRAPPERS.glob("*.sh.in"))
        self.assertTrue(found, f"no wrapper templates under {WRAPPERS}")
        self.assertEqual(found, sorted(PUBLISHED), "a template was added or removed")

    def test_no_template_changed_without_the_package_being_re_synced(self):
        drifted = []
        for name, expected in sorted(PUBLISHED.items()):
            path = WRAPPERS / name
            actual = _sha(path)
            if actual != expected:
                drifted.append(f"{name}\n      recorded {expected}\n      actual   {actual}")
        self.assertFalse(
            drifted,
            "wrapper templates changed here and the published package has not been told:\n  "
            + "\n  ".join(drifted)
            + f"\n\nCopy the changed file(s) to {PACKAGE_URL}, run its tests, commit there, then update "
            "PUBLISHED above IN THIS SAME CHANGE. Two sources of truth only stay equal if changing one "
            "is made to hurt.",
        )


if __name__ == "__main__":
    unittest.main()
