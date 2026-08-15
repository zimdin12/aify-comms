"""The `send_channel_message` split, re-proved against the real code on every run.

WHAT WAS EXTRACTED: the send-time coldstart for COLD managed channel members — the part of a 184-line
handler that reaches OUTSIDE the message write to wake workers. Everything around it inserts rows;
this spawns processes, which is a different job with a different failure mode.

WHY THE BOUNDARY IS WHERE IT IS, and this is the interesting part. The obvious block was the whole
`if should_trigger and dispatch_recipients:` — 54 lines — and it is NOT extractable, because it reads
`prefer_steer`, which the handler binds only inside an EARLIER and SEPARATE `if should_trigger and
recipients:`. That is exactly the shape that broke this suite once in `send_message` (48 tests, a 500
on the route) and prompted the conditionally-bound-argument rule. The gate would refuse it, correctly.

THE EXISTING CODE IS SAFE, and it is worth writing down why, because nothing states it. Line 465 can
only run when line 432 ran: `dispatch_recipients` is derived from `recipients`, so an empty
`recipients` makes both conditions false, and `should_trigger` gates both. So `prefer_steer` is always
bound by the time it is read — by an implication spanning thirty lines and two derivations. Safe, but
not locally evident, which is why the split stops before it rather than papering over it.

WHAT THIS DOES NOT DO: it proves the extraction is inert. Whether the coldstart itself is correct is
the dispatch and spawn tests' job.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
# `send_channel_message` moved to `routers/channel_send.py` in v0.5.4 as a private cluster, with
# its dedup helper and the window constant. A round-trip proof names the module holding the
# CALLER, so a relocation must touch it — see the one-line pin below.
CALLER = REPO / "service" / "routers" / "channel_send.py"
COLDSTART = REPO / "service" / "api_core" / "channel_coldstart.py"

MODULES = (CALLER, COLDSTART)
FIXTURE = Path(__file__).resolve().parent / "data" / "send_channel_message_before_split.py"

SOURCE_FUNCTION = "send_channel_message"
EXTRACTIONS = ["_coldstart_cold_channel_members"]
OWNERS = {"_coldstart_cold_channel_members": COLDSTART}


def _combined_split_source() -> str:
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


class SendChannelMessageSplitIsInertTests(unittest.TestCase):
    def test_the_extraction_inlines_back_to_the_original(self):
        fixture_src = FIXTURE.read_text(encoding="utf-8")
        original = next(
            n for n in ast.parse(fixture_src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )
        assert_extractions_preserve_behaviour(
            ast.get_source_segment(fixture_src, original), _combined_split_source(), EXTRACTIONS)

    def test_the_source_function_is_still_where_this_proof_looks(self):
        """`CALLER` is a location pin, and a relocation is what breaks it.

        Added when `send_channel_message` moved out of `channels.py` in v0.5.4. The round trip
        already fails then — it cannot find the caller to inline into — but it fails as a
        gate-internal error about a missing definition. This says the true thing in one line.
        """
        declared = {
            n.name for n in ast.parse(CALLER.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn(
            SOURCE_FUNCTION, declared,
            f"{SOURCE_FUNCTION} is not declared in {CALLER.name}. If it was relocated, repoint "
            "CALLER at its new module — this proof names the file holding the caller, so a move "
            "must touch it.",
        )

    def test_the_fixture_is_the_function_it_claims_to_be(self):
        names = {
            n.name for n in ast.parse(FIXTURE.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        self.assertIn(SOURCE_FUNCTION, names)

    def test_the_fixture_was_not_captured_with_a_mangled_decode(self):
        """`subprocess.run(text=True)` decodes with the Windows locale and mangles every dash."""
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("�", text, "fixture contains U+FFFD replacement characters")
        live = CALLER.read_text(encoding="utf-8")
        expected = ast.get_source_segment(live, next(
            n for n in ast.parse(live).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )) or ""
        if expected.count("—"):
            self.assertGreater(text.count("—"), 0, "fixture looks locale-mangled, not utf-8")

    def test_the_helper_is_not_still_inline(self):
        declared = {
            n.name for n in ast.parse(CALLER.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for helper in EXTRACTIONS:
            self.assertNotIn(helper, declared, f"{helper} is back in channel_send.py; this proof is vacuous")

    def test_exactly_one_module_declares_the_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [
                path for path in MODULES
                if any(
                    isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == helper
                    for n in ast.parse(path.read_text(encoding="utf-8")).body
                )
            ]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once")

    def test_the_leaf_does_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent."""
        for node in ast.walk(ast.parse(COLDSTART.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"channel_coldstart.py imports upward from {node.module}",
                )

    def test_prefer_steer_did_NOT_travel(self):
        """The boundary is where it is because of this name; assert it stayed behind.

        `prefer_steer` is bound only inside an earlier, separate `if`, so passing it into a helper
        would be the conditionally-bound argument that broke this suite once. A later slice widening
        this extraction upward would silently reintroduce that, and the round trip would not see it —
        reconstruction puts the read back inside the guard it moved out of.
        """
        helper_src = COLDSTART.read_text(encoding="utf-8")
        helper = next(
            n for n in ast.parse(helper_src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == EXTRACTIONS[0]
        )
        params = {a.arg for a in helper.args.args}
        self.assertNotIn("prefer_steer", params)
        used = {n.id for n in ast.walk(helper) if isinstance(n, ast.Name)}
        self.assertNotIn("prefer_steer", used, "the helper must not read it either")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
