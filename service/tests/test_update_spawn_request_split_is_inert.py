"""The `update_spawn_request` split, re-proved against the real code on every run.

Same shape as the other split proofs here: proving a split once at refactor time proves the commit,
running the round trip in the suite proves it STAYS true.

WHAT WAS EXTRACTED, and it is the largest single extraction in this series: everything that happens
the moment a spawn reports RUNNING. `update_spawn_request` was 384 lines and 299 of them were that
one branch — the transition where a spawn REQUEST becomes a live agent. It moves whole because it is
one subject: upsert the agent the spec described, open its session, bind a terminal if the
environment backs one, and deliver the initial message the spawn existed to carry.

THE ROUND TRIP IS WORTH MORE HERE THAN ANYWHERE ELSE IN THE SERIES. Three hundred lines is past what
review reliably catches, and every write in them is on the path that turns a request into something
an operator can talk to. A silent change does not raise — it produces an agent that half exists.

THE SUBSTITUTION, declared rather than left to be noticed: the helper lives in
`service/api_core/running_spawn.py`, because leaving it in the router would not have reduced it —
that was the point. The extract-method gate needs the caller and the helper in one tree, so the
sources are CONCATENATED for the proof.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
SPAWN_REQUESTS = REPO / "service" / "routers" / "spawn_requests.py"
RUNNING = REPO / "service" / "api_core" / "running_spawn.py"
FIXTURE = Path(__file__).resolve().parent / "data" / "update_spawn_request_before_split.py"

SOURCE_FUNCTION = "update_spawn_request"
EXTRACTIONS = ["_settle_running_spawn"]

#: Where each helper is expected to be declared. PER HELPER, over every module below.
OWNERS = {"_settle_running_spawn": RUNNING}

MODULES = (SPAWN_REQUESTS, RUNNING)


def _combined_split_source() -> str:
    """The caller and every extracted helper in one tree, for the inline-back comparison."""
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)


def _declared(path: Path) -> set[str]:
    return {
        n.name for n in ast.parse(path.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }


def _helper() -> ast.AST:
    return next(
        n for n in ast.parse(RUNNING.read_text(encoding="utf-8")).body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == EXTRACTIONS[0]
    )


class UpdateSpawnRequestSplitIsInertTests(unittest.TestCase):
    def test_the_extraction_inlines_back_to_the_original(self):
        fixture_src = FIXTURE.read_text(encoding="utf-8")
        original = next(
            n for n in ast.parse(fixture_src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )
        assert_extractions_preserve_behaviour(
            ast.get_source_segment(fixture_src, original), _combined_split_source(), EXTRACTIONS)

    def test_the_fixture_is_the_function_it_claims_to_be(self):
        """A fixture that stopped containing the function would make the test above vacuous."""
        self.assertIn(SOURCE_FUNCTION, _declared(FIXTURE))

    def test_the_fixture_was_not_captured_with_a_mangled_decode(self):
        """`subprocess.run(text=True)` decodes with the Windows locale and mangles every dash."""
        text = FIXTURE.read_text(encoding="utf-8")
        self.assertNotIn("�", text, "fixture contains U+FFFD replacement characters")
        self.assertGreater(text.count("—"), 0, "fixture looks locale-mangled, not utf-8")

    def test_the_helper_is_not_still_inline(self):
        """If the split were reverted, the round trip would pass by having nothing to inline."""
        for helper in EXTRACTIONS:
            self.assertNotIn(
                helper, _declared(SPAWN_REQUESTS),
                f"{helper} is back in spawn_requests.py; this proof is vacuous")

    def test_exactly_one_module_declares_EACH_helper(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        for helper, owner in OWNERS.items():
            owners = [path for path in MODULES if helper in _declared(path)]
            self.assertEqual([owner], owners, f"{helper} must be declared exactly once, in {owner.name}")

    def test_the_leaf_does_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent.

        Twelve helpers are called from inside this block and every one of them already had a leaf
        owner, which is the only reason an extraction this size was available at all. The turn-busy
        block in `agent_heartbeat` was blocked for a release by exactly one callee that did not.
        """
        for node in ast.walk(ast.parse(RUNNING.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"running_spawn.py imports upward from {node.module}",
                )

    def test_the_helper_OWNS_NO_TRANSACTION(self):
        """The commit stays in the route, and at this size that matters more than usual.

        These writes must all land or none of them: an agent row without its session, or a session
        without the message that justified the spawn, is worse than a failed spawn because it looks
        like a working one. Asked of the CODE rather than the file — a substring search over the
        source would match this module's own prose, the way it once did in the rename proof.
        """
        called = {
            node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
            for node in ast.walk(_helper()) if isinstance(node, ast.Call)
        }
        for forbidden in ("commit", "rollback", "get_db"):
            self.assertNotIn(
                forbidden, called,
                f"the running-spawn settlement must not call {forbidden}() — the route owns the transaction")

    def test_the_session_id_is_RETURNED_not_mutated(self):
        """The one live-out, and the reason the helper has a return at all.

        `session_id` is generated here when the request arrived without one, and the caller writes it
        back to `spawn_requests` afterwards. Left as a bare assignment it would be a helper local
        after the split and the caller would persist the OLD value — a spawn that ran but whose row
        never learned which session it became.
        """
        returned = _helper().body[-1]
        self.assertIsInstance(returned, ast.Return)
        self.assertEqual("session_id", returned.value.id)
        call = next(
            n for n in ast.walk(ast.parse(SPAWN_REQUESTS.read_text(encoding="utf-8")))
            if isinstance(n, ast.Assign) and isinstance(n.value, ast.Await)
            and getattr(n.value.value.func, "id", "") == EXTRACTIONS[0]
        )
        self.assertEqual("session_id", call.targets[0].id, "the caller must rebind the same name")

    def test_the_LOGGER_NAME_is_preserved(self):
        """The one thing about this move that was NOT byte-identical, pinned so it stays deliberate.

        The block contains a warning about an eager-PTY failure, and the comment beside it records
        that a silent failure there hid an AttributeError for two live restarts. Giving the new
        module its own logger name would move that line to a channel nobody greps — the same outcome
        as silencing it. The leaf therefore keeps the ROUTER's logger name, which looks wrong and is
        the point.
        """
        import re

        router_src = SPAWN_REQUESTS.read_text(encoding="utf-8")
        leaf_src = RUNNING.read_text(encoding="utf-8")
        names = {
            path: set(re.findall(r'logging\.getLogger\("([^"]+)"\)', src))
            for path, src in ((SPAWN_REQUESTS, router_src), (RUNNING, leaf_src))
        }
        self.assertEqual(
            names[SPAWN_REQUESTS], names[RUNNING],
            "the extracted warning must keep logging under the name operators already grep")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
