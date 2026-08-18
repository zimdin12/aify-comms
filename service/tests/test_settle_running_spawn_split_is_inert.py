"""The `_settle_running_spawn` split, re-proved against the real code on every run.

WHAT WAS EXTRACTED: four of the things that happen when a spawn request becomes a live worker —
writing the `agent_sessions` row, migrating the bridge id onto the terminal actually serving the
session, handing the waiting work to dispatch, and giving the worker a managed PTY. 311 -> 166.

THE HELPERS STAYED IN THIS MODULE, which is the unusual part and is deliberate. `running_spawn.py` is
already under the 400-line target, so the problem was never the file — it was one function long enough
that its phases could not be seen at once. Moving the helpers to a new module would have split one
subject across two files to fix a size problem that did not exist, and the phases are not a shared
subject: a session upsert, bridge migration, dispatch handoff and PTY creation have nothing in common
except the moment they run.

THE SESSION UPSERT WAS FIFTY LINES OF ONE SQL STATEMENT, which is why it counted: not a decision, just
a large opaque middle. Its comment travelled with it, because that comment records why the statement
is an UPSERT rather than INSERT OR REPLACE and what broke when it was not — reasoning that is one edit
from being lost once separated from the SQL it explains.

WHY IT WAS EXTRACTABLE AT ALL: `_settle_running_spawn` contains no `return` anywhere. Every other
large function reached in this release has been a guard chain — `_bridge_claim_block_reason` is 208
lines of nothing but early exits at four depths, and extract-method cannot judge those. A function
that only does things, rather than deciding whether to keep going, is the shape that splits cleanly.

WHAT THIS DOES NOT DO: it proves the extractions are inert. Whether the settlement is correct is
`test_running_spawn_reconciliation.py`'s job.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

from service.tests.extract_method import assert_extractions_preserve_behaviour

REPO = Path(__file__).resolve().parent.parent.parent
SPAWN = REPO / "service" / "api_core" / "running_spawn.py"

MODULES = (SPAWN,)
FIXTURE = Path(__file__).resolve().parent / "data" / "settle_running_spawn_before_split.py"

SOURCE_FUNCTION = "_settle_running_spawn"
EXTRACTIONS = [
    "_migrate_bridge_id_onto_live_terminal",
    "_hand_settled_spawn_to_dispatch",
    "_ensure_pty_for_settled_spawn",
    "_upsert_running_agent_session",
]
OWNERS = {name: SPAWN for name in EXTRACTIONS}


def _combined_split_source() -> str:
    return "\n\n".join(p.read_text(encoding="utf-8") for p in MODULES)



#: DECLARED EDIT SINCE THE SPLIT (2026-08-18). `_hand_settled_spawn_to_dispatch` used to create the
#: initial-message run with `message_id=None`, so a spawned agent's inbox was EMPTY while the
#: dispatch text told it to read the brief there, and the dispatch event carried no id for the agent
#: to put in `inReplyTo`. An end-to-end probe reported both in its first reply.
#:
#: The fix stores a real message for the brief, which changes a body this proof reconstructs. The
#: change is written down here with BOTH texts rather than the fixture being re-captured: re-capturing
#: would erase the baseline, after which this gate proves only that the split is inert relative to
#: whatever the code is today — not a claim about the extraction at all.
EDITED_SINCE = [
    (
        '                settings_for_runs = await _load_settings(db)\n                sender = row["created_by"] or "dashboard"\n                subject = row["subject"] or f"Spawn {row[\'agent_id\']}"\n                body = row["initial_message"]\n                priority = row["priority"] or "normal"\n\n                # A REAL MESSAGE BEHIND THE BRIEF, added 2026-08-18 after an end-to-end probe caught\n                # its absence. This run used to be created with `message_id=None`, so:\n                #   * the spawned agent\'s `comms_inbox` was EMPTY while the dispatch text it received\n                #     said "Full details are in the inbox. Read them there if you need the complete\n                #     context" — an instruction that could not be followed;\n                #   * the dispatch event carried `message_id=""`, so the agent had no id to put in\n                #     `inReplyTo` and could not thread its reply to the brief it was answering.\n                # The probe reported both in its first reply, which is exactly what a probe is for.\n                #\n                # The brief IS a message — one agent asking another to do something — so it gets a row\n                # like any other rather than a special case downstream.\n                message_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"\n                await db.execute(\n                    """\n                    INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body,\n                                          priority, dispatch_requested, in_reply_to, timestamp)\n                    VALUES (?,?,?,?,?,?,?,?,?,?,?)\n                    """,\n                    (message_id, sender, row["agent_id"], "direct", "request", subject, body,\n                     priority, 1, None, int(time.time() * 1000)),\n                )\n                runs = await _create_dispatch_runs(\n                    db,\n                    [row["agent_id"]],\n                    from_agent=sender,\n                    message_type="request",\n                    subject=subject,\n                    body=body,\n                    priority=priority,\n                    in_reply_to=None,\n                    dispatch_mode="start_if_possible",\n                    execution_mode=(\n                        "channel"\n                        if _managed_via_wrapper_for_runtime(settings_for_runs, row["runtime"] or "")\n                        else "managed"\n                    ),\n                    requested_runtime=row["runtime"],\n                    message_id=message_id,',
        '                settings_for_runs = await _load_settings(db)\n                runs = await _create_dispatch_runs(\n                    db,\n                    [row["agent_id"]],\n                    from_agent=row["created_by"] or "dashboard",\n                    message_type="request",\n                    subject=row["subject"] or f"Spawn {row[\'agent_id\']}",\n                    body=row["initial_message"],\n                    priority=row["priority"] or "normal",\n                    in_reply_to=None,\n                    dispatch_mode="start_if_possible",\n                    execution_mode=(\n                        "channel"\n                        if _managed_via_wrapper_for_runtime(settings_for_runs, row["runtime"] or "")\n                        else "managed"\n                    ),\n                    requested_runtime=row["runtime"],\n                    message_id=None,',
    ),
]

class SettleRunningSpawnSplitIsInertTests(unittest.TestCase):
    def test_the_extractions_inline_back_to_the_original(self):
        fixture_src = FIXTURE.read_text(encoding="utf-8")
        original = next(
            n for n in ast.parse(fixture_src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )
        assert_extractions_preserve_behaviour(
            ast.get_source_segment(fixture_src, original), _combined_split_source(), EXTRACTIONS, EDITED_SINCE)

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
        live = SPAWN.read_text(encoding="utf-8")
        expected = ast.get_source_segment(live, next(
            n for n in ast.parse(live).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )) or ""
        if expected.count("—"):
            self.assertGreater(text.count("—"), 0, "fixture looks locale-mangled, not utf-8")

    def test_the_caller_no_longer_contains_the_bodies(self):
        """A reverted split would round-trip by having nothing to inline.

        The helpers live in the SAME module as their caller, so "is it still declared here" cannot be
        the check — it is declared here on purpose. What must be true is that the CALLER's own body no
        longer holds them, which is what the call sites prove.
        """
        src = SPAWN.read_text(encoding="utf-8")
        caller = next(
            n for n in ast.parse(src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )
        called = {
            node.func.id for node in ast.walk(caller)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        for helper in EXTRACTIONS:
            self.assertIn(helper, called, f"{helper} is not called; the split was reverted")

    def test_each_helper_is_declared_exactly_once(self):
        self.assertEqual(sorted(OWNERS), sorted(EXTRACTIONS), "every extraction needs a declared owner")
        declared = [
            n.name for n in ast.parse(SPAWN.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        ]
        for helper in EXTRACTIONS:
            self.assertEqual(1, declared.count(helper), f"{helper} must be declared exactly once")

    def test_the_module_does_not_import_upward(self):
        """An api_core leaf reaching into a router — or the control plane — is the cycle to prevent."""
        for node in ast.walk(ast.parse(SPAWN.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module:
                self.assertFalse(
                    node.module.startswith("service.routers")
                    or node.module == "service.control_plane",
                    f"running_spawn.py imports upward from {node.module}",
                )

    def test_the_settlement_still_has_no_early_exit(self):
        """The property that made this splittable, pinned so a later edit cannot quietly remove it.

        `_settle_running_spawn` does things; it does not decide whether to keep going. Add a `return`
        mid-body and the next extraction from it becomes unprovable — the gate would refuse it, and
        the reason would look like a gate problem rather than a change to this function's shape.
        """
        caller = next(
            n for n in ast.parse(SPAWN.read_text(encoding="utf-8")).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )
        # A SINGLE TRAILING return is not an early exit — this function ends `return session_id`, and
        # my first version of this assertion forgot that and failed on correct code. What must stay
        # absent is a return anywhere OTHER than the final statement.
        self.assertIsInstance(caller.body[-1], ast.Return, "the settlement should end by returning")
        early = [
            node for stmt in caller.body[:-1]
            for node in ast.walk(stmt)
            if isinstance(node, ast.Return)
        ]
        self.assertEqual([], early, "the settlement gained an early exit")

    def test_the_fixture_is_tracked(self):
        self.assertTrue(FIXTURE.exists())
        self.assertGreater(len(FIXTURE.read_text(encoding="utf-8")), 1000)


if __name__ == "__main__":
    unittest.main()
