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
#: NESTED as of v0.5.4. `_settle_running_spawn` was itself split into three helpers, so
#: inlining it alone no longer reproduces this fixture — its own callees have to collapse
#: into it first. The verifier resolves that order itself; the list just has to be complete.
EXTRACTIONS = [
    "_settle_running_spawn",
    "_migrate_bridge_id_onto_live_terminal",
    "_hand_settled_spawn_to_dispatch",
    "_ensure_pty_for_settled_spawn",
    "_upsert_running_agent_session",
]

#: Where each helper is expected to be declared. PER HELPER, over every module below.
OWNERS = {
    "_settle_running_spawn": RUNNING,
    "_migrate_bridge_id_onto_live_terminal": RUNNING,
    "_hand_settled_spawn_to_dispatch": RUNNING,
    "_ensure_pty_for_settled_spawn": RUNNING,
    "_upsert_running_agent_session": RUNNING,
}

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



#: DECLARED EDIT SINCE THE SPLIT (2026-08-18). `_hand_settled_spawn_to_dispatch` used to create the
#: initial-message run with `message_id=None`, so a spawned agent's inbox was EMPTY while the
#: dispatch text told it to read the brief there, and the dispatch event carried no id for the agent
#: to put in `inReplyTo`. An end-to-end probe reported both in its first reply.
#:
#: The fix stores a real message for the brief, which changes a body this proof reconstructs. The
#: change is written down here with BOTH texts rather than the fixture being re-captured: re-capturing
#: would erase the baseline, after which this gate proves only that the split is inert relative to
#: whatever the code is today — not a claim about the extraction at all.
#: DECLARED EDIT, 2026-08-29. Six ended-session statuses were spelled out by hand in NINE SQL
#: strings across five modules while `ENDED_AGENT_SESSION_STATUSES` owned them. They now
#: interpolate `ENDED_AGENT_SESSION_STATUS_SQL`, rendered once from that constant. Undone here
#: rather than re-captured: re-capturing the fixture would erase the pre-split baseline and
#: leave this proving only that the split is inert relative to whatever the code is today.
#: DECLARED EDIT, 2026-08-29. Sixteen live-terminal filters spelled their status set out by hand;
#: they now interpolate a fragment from `api_core/terminal_status.py`. Undone here rather than
#: re-captured, so the pre-split baseline survives.
EDITED_SINCE = [
    (
        '\nfrom service.api_core.terminal_status import TERMINAL_LIVE_FILTER_SQL\nfrom service.api_core.agent_sessions import ENDED_AGENT_SESSION_STATUS_SQL',
        '\nfrom service.api_core.agent_sessions import ENDED_AGENT_SESSION_STATUS_SQL',
    ),
    (
        '                live_terminal = await (await db.execute(\n                    f"""\n                    SELECT id, status, command, workspace, session_id FROM terminal_sessions',
        '                live_terminal = await (await db.execute(\n                    """\n                    SELECT id, status, command, workspace, session_id FROM terminal_sessions',
    ),
    (
        "                      AND id NOT LIKE 'vterm_%'\n                      AND status IN {TERMINAL_LIVE_FILTER_SQL}\n                      AND datetime(COALESCE(NULLIF(created_at, ''), '1970-01-01'))",
        "                      AND id NOT LIKE 'vterm_%'\n                      AND status IN ('starting', 'attached', 'running', 'active', 'idle', 'recovering')\n                      AND datetime(COALESCE(NULLIF(created_at, ''), '1970-01-01'))",
    ),
    (
        '                        f"""\n                        UPDATE agent_sessions\n                        SET terminal_id = ?, terminal_status = ?,\n                            terminal_command = ?, terminal_workspace = ?,\n                            -- Binding a LIVE terminal is the authoritative "backing (re)started"\n                            -- event: promote a dead-state denorm back to running, else the row\n                            -- keeps the PREVIOUS backing\'s \'stopped\' and the Console label reads\n                            -- "Console stopped" for a live attached terminal forever (cms-manager,\n                            -- 2026-06-10; the display deriver deliberately never promotes).\n                            status = CASE WHEN status IN {ENDED_AGENT_SESSION_STATUS_SQL}\n                                          THEN \'running\' ELSE status END,\n                            ended_at = CASE WHEN status IN {ENDED_AGENT_SESSION_STATUS_SQL}\n                                            THEN NULL ELSE ended_at END\n                        WHERE id = ?',
        '                        """\n                        UPDATE agent_sessions\n                        SET terminal_id = ?, terminal_status = ?,\n                            terminal_command = ?, terminal_workspace = ?,\n                            -- Binding a LIVE terminal is the authoritative "backing (re)started"\n                            -- event: promote a dead-state denorm back to running, else the row\n                            -- keeps the PREVIOUS backing\'s \'stopped\' and the Console label reads\n                            -- "Console stopped" for a live attached terminal forever (cms-manager,\n                            -- 2026-06-10; the display deriver deliberately never promotes).\n                            status = CASE WHEN status IN (\'stopped\',\'ended\',\'failed\',\'lost\',\'cancelled\',\'completed\')\n                                          THEN \'running\' ELSE status END,\n                            ended_at = CASE WHEN status IN (\'stopped\',\'ended\',\'failed\',\'lost\',\'cancelled\',\'completed\')\n                                            THEN NULL ELSE ended_at END\n                        WHERE id = ?',
    ),
    (
        '                settings_for_runs = await _load_settings(db)\n                sender = row["created_by"] or "dashboard"\n                subject = row["subject"] or f"Spawn {row[\'agent_id\']}"\n                body = row["initial_message"]\n                priority = row["priority"] or "normal"\n\n                # A REAL MESSAGE BEHIND THE BRIEF, added 2026-08-18 after an end-to-end probe caught\n                # its absence. This run used to be created with `message_id=None`, so:\n                #   * the spawned agent\'s `comms_inbox` was EMPTY while the dispatch text it received\n                #     said "Full details are in the inbox. Read them there if you need the complete\n                #     context" — an instruction that could not be followed;\n                #   * the dispatch event carried `message_id=""`, so the agent had no id to put in\n                #     `inReplyTo` and could not thread its reply to the brief it was answering.\n                # The probe reported both in its first reply, which is exactly what a probe is for.\n                #\n                # The brief IS a message — one agent asking another to do something — so it gets a row\n                # like any other rather than a special case downstream.\n                message_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"\n                await db.execute(\n                    """\n                    INSERT INTO messages (id, from_agent, to_agent, source, type, subject, body,\n                                          priority, dispatch_requested, in_reply_to, timestamp)\n                    VALUES (?,?,?,?,?,?,?,?,?,?,?)\n                    """,\n                    (message_id, sender, row["agent_id"], "direct", "request", subject, body,\n                     priority, 1, None, int(time.time() * 1000)),\n                )\n                runs = await _create_dispatch_runs(\n                    db,\n                    [row["agent_id"]],\n                    from_agent=sender,\n                    message_type="request",\n                    subject=subject,\n                    body=body,\n                    priority=priority,\n                    in_reply_to=None,\n                    dispatch_mode="start_if_possible",\n                    execution_mode=(\n                        "channel"\n                        if _managed_via_wrapper_for_runtime(settings_for_runs, row["runtime"] or "")\n                        else "managed"\n                    ),\n                    requested_runtime=row["runtime"],\n                    message_id=message_id,',
        '                settings_for_runs = await _load_settings(db)\n                runs = await _create_dispatch_runs(\n                    db,\n                    [row["agent_id"]],\n                    from_agent=row["created_by"] or "dashboard",\n                    message_type="request",\n                    subject=row["subject"] or f"Spawn {row[\'agent_id\']}",\n                    body=row["initial_message"],\n                    priority=row["priority"] or "normal",\n                    in_reply_to=None,\n                    dispatch_mode="start_if_possible",\n                    execution_mode=(\n                        "channel"\n                        if _managed_via_wrapper_for_runtime(settings_for_runs, row["runtime"] or "")\n                        else "managed"\n                    ),\n                    requested_runtime=row["runtime"],\n                    message_id=None,',
    ),
]

class UpdateSpawnRequestSplitIsInertTests(unittest.TestCase):
    def test_the_extraction_inlines_back_to_the_original(self):
        fixture_src = FIXTURE.read_text(encoding="utf-8")
        original = next(
            n for n in ast.parse(fixture_src).body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == SOURCE_FUNCTION
        )
        assert_extractions_preserve_behaviour(
            ast.get_source_segment(fixture_src, original), _combined_split_source(), EXTRACTIONS, EDITED_SINCE)

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
